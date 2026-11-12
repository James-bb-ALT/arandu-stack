"""Motores Piper (CPU/streaming) e proxy de GPU (WAV por HTTP)."""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
from pathlib import Path
import threading
from concurrent.futures import TimeoutError as FutureTimeout
import time
import urllib.request
import wave
from dataclasses import dataclass
from typing import AsyncIterator, Iterator

from .config import Config
from .voices import VoiceSpec

LOG = logging.getLogger(__name__)


def load_piper(voice, threads):
    """Piper 1.8 public constructor, with a real ORT budget (OMP is insufficient)."""
    import onnxruntime as ort
    from piper import PiperVoice
    if threads == 0:
        return PiperVoice.load(str(voice.model), use_cuda=False)
    from piper.config import PiperConfig
    if type(threads) is not int or not 1<=threads<=8:
        raise ValueError('Piper threads must be between 1 and 8')
    options=ort.SessionOptions()
    options.intra_op_num_threads=threads
    options.inter_op_num_threads=1
    options.execution_mode=ort.ExecutionMode.ORT_SEQUENTIAL
    config=json.loads(Path(str(voice.model)+'.json').read_text(encoding='utf-8'))
    return PiperVoice(config=PiperConfig.from_dict(config),
        session=ort.InferenceSession(str(voice.model),sess_options=options,providers=['CPUExecutionProvider']))


@dataclass(frozen=True)
class PCMChunk:
    data: bytes
    rate: int
    width: int
    channels: int


def split_pcm(pcm: bytes, rate: int, width: int, channels: int, target_frames: int = 2048) -> Iterator[PCMChunk]:
    frame = width * channels
    if frame < 1 or len(pcm) % frame:
        raise ValueError("Áudio PCM com frames inválidos")
    size = frame * target_frames
    for offset in range(0, len(pcm), size):
        yield PCMChunk(pcm[offset:offset + size], rate, width, channels)


def parse_wav(wav_bytes: bytes, limit: int = 32 * 1024 * 1024) -> Iterator[PCMChunk]:
    if len(wav_bytes) > limit:
        raise ValueError("WAV remoto excede limite")
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
        if wav.getcomptype() != "NONE" or wav.getsampwidth() != 2 or wav.getnchannels() != 1:
            raise ValueError("GPU precisa devolver WAV PCM16 mono")
        while True:
            audio = wav.readframes(2048)
            if not audio:
                break
            yield PCMChunk(audio, wav.getframerate(), 2, 1)


class SpeechEngine:
    """Cache de um modelo na CPU, inferência serializada e isolamento de backend."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._lock = threading.RLock()
        self._loaded_name: str | None = None
        self._loaded_model = None
        self._pocket = None
        self._async_slots = asyncio.Semaphore(1)
        self._requests = 0
        self._native_jobs = 0
        self.recent = {}

    @property
    def pocket_loaded(self) -> bool:
        return self._pocket is not None and self._pocket.loaded

    def warm_pocket(self) -> None:
        self._get_pocket().load()

    def _get_pocket(self):
        if self._pocket is None:
            from .pocket import PocketEngine
            self._pocket = PocketEngine(self.cfg)
        return self._pocket

    def drop_pocket_voice(self, name: str) -> None:
        if self._pocket is not None:
            self._pocket.drop_voice(name)

    def prepare_pocket_voice(self, wav, target) -> None:
        self._get_pocket().prepare_voice(wav, target)

    def preload(self, voice: VoiceSpec) -> None:
        if voice.backend != "piper":
            return
        with self._lock:
            self._load(voice)

    def _load(self, voice: VoiceSpec):
        if voice.backend != "piper" or voice.model is None:
            raise ValueError("Voz não é Piper")
        if self._loaded_name != voice.name:
            LOG.info("Carregando modelo local: %s", voice.name)
            self._loaded_model = load_piper(voice,self.cfg.piper_threads if self.cfg.piper_thread_budget else 0)
            self._loaded_name = voice.name
        return self._loaded_model

    def _piper_stream(self, text: str, voice: VoiceSpec) -> Iterator[PCMChunk]:
        os.environ["OMP_NUM_THREADS"] = str(self.cfg.piper_threads)
        # A trava precisa cobrir síntese inteira para não substituir o modelo em uso.
        with self._lock:
            model = self._load(voice)
            for audio_chunk in model.synthesize(text):
                yield from split_pcm(
                    audio_chunk.audio_int16_bytes,
                    audio_chunk.sample_rate,
                    audio_chunk.sample_width,
                    audio_chunk.sample_channels,
                )

    def _remote_stream(self, text: str, voice: VoiceSpec) -> Iterator[PCMChunk]:
        if not self.cfg.remote_url:
            raise RuntimeError("ARANDU_REMOTE_URL não configurada")
        if not self.cfg.remote_token:
            raise RuntimeError("ARANDU_REMOTE_TOKEN obrigatório para proxy GPU")
        if not self.cfg.remote_url.startswith(("http://", "https://")):
            raise ValueError("ARANDU_REMOTE_URL deve usar HTTP(S)")
        payload = json.dumps({"text": text, "voice": voice.remote_voice}).encode("utf-8")
        req = urllib.request.Request(
            self.cfg.remote_url + "/synthesize", data=payload,
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.cfg.remote_token},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.cfg.remote_timeout) as response:
            content_type = response.headers.get("Content-Type", "")
            if "audio/wav" not in content_type and "audio/x-wav" not in content_type:
                raise ValueError("Servidor remoto não retornou áudio WAV")
            raw = response.read(32 * 1024 * 1024 + 1)
        yield from parse_wav(raw)

    def stream_sync(self, text: str, voice: VoiceSpec) -> Iterator[PCMChunk]:
        if voice.backend == "remote":
            yield from self._remote_stream(text, voice)
        elif voice.backend == "piper":
            yield from self._piper_stream(text, voice)
        elif voice.backend == "pocket":
            yield from self._get_pocket().stream(text, voice)
        else:
            raise ValueError("Backend desconhecido")

    async def stream(self, text: str, voice: VoiceSpec) -> AsyncIterator[PCMChunk]:
        if self._requests >= 1 + max(0, min(16, self.cfg.queue_limit)):
            raise RuntimeError('TTS queue_saturated')
        self._requests += 1
        queued = time.perf_counter()
        try:
            async with asyncio.timeout(60), self._async_slots:
                started = time.perf_counter()
                first_ms = None
                samples = 0
                try:
                    async for chunk in self._stream(text, voice):
                        if first_ms is None:
                            first_ms = (time.perf_counter()-started)*1000
                        samples += len(chunk.data)//(chunk.width*chunk.channels)
                        yield chunk
                finally:
                    self.recent = {'queue_ms':(started-queued)*1000,'first_protocol_chunk_ms':first_ms,
                        'total_ms':(time.perf_counter()-started)*1000,'samples':samples,
                        'playback':'not_observed','buffer_chunks_limit':8}
        finally:
            self._requests -= 1

    async def _stream(self, text: str, voice: VoiceSpec) -> AsyncIterator[PCMChunk]:
        """Produz resultados na thread de inferência sem bloquear loop Wyoming."""
        if self._native_jobs:
            raise RuntimeError('TTS native_worker_busy')
        self._native_jobs += 1
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[object] = asyncio.Queue(maxsize=8)
        stop = object()
        cancel = threading.Event()

        def send(item):
            if cancel.is_set():
                return False
            pending = queue.put(item)
            try:
                future = asyncio.run_coroutine_threadsafe(pending,loop)
            except RuntimeError:
                pending.close()
                return False
            try:
                while not cancel.is_set():
                    try:
                        future.result(timeout=.1)
                        return True
                    except FutureTimeout:
                        pass
                return False
            finally:
                if not future.done():
                    future.cancel()

        def produce() -> None:
            try:
                for chunk in self.stream_sync(text, voice):
                    if not send(chunk):
                        break
            except BaseException as exc:
                send(exc)
            finally:
                send(stop)

        task = asyncio.create_task(asyncio.to_thread(produce))
        def finished(worker):
            self._native_jobs -= 1
            if not worker.cancelled():
                worker.exception()
        task.add_done_callback(finished)
        exhausted = False
        try:
            while True:
                item = await queue.get()
                if item is stop:
                    exhausted = True
                    break
                if isinstance(item, BaseException):
                    raise item
                assert isinstance(item, PCMChunk)
                yield item
        finally:
            cancel.set()
            # Não aguarda inferência ao cancelar cliente; a trava é liberada no worker.
            if exhausted or task.done():
                await task
            else:
                task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
