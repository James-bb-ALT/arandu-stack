"""Servidor Wyoming nativo (sem componentes customizados no Home Assistant)."""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import subprocess
import sys
import time
from functools import partial
from pathlib import Path

from . import __version__
from .config import Config
from .engine import SpeechEngine
from .text import SentenceBuffer, normalize_text
from .voices import VoiceSpec, catalogue, valid_voice_id

LOG = logging.getLogger(__name__)


def ensure_default_model(cfg: Config) -> None:
    """Garante Piper base mesmo se a voz padrão estiver no Pocket ou GPU."""
    cfg.voices_dir.mkdir(parents=True, exist_ok=True)
    found = catalogue(cfg.voices_dir, pocket_enabled=cfg.pocket_enabled)
    required = (cfg.default_voice if not cfg.default_voice.startswith('pocket-')
                and (cfg.default_voice not in found or found[cfg.default_voice].backend == 'piper')
                else cfg.fallback_voice)
    if required in found and found[required].backend == 'piper':
        return
    if not cfg.auto_download:
        LOG.warning('Modelo Piper %s não encontrado em %s', required, cfg.voices_dir)
        return
    if not valid_voice_id(required):
        raise ValueError('Nome de voz inválido')
    cmd = [sys.executable, '-m', 'piper.download_voices', '--data-dir', str(cfg.voices_dir), required]
    LOG.info('Baixando voz Piper base: %s', required)
    subprocess.run(cmd, check=True, timeout=600)
    if required not in catalogue(cfg.voices_dir, pocket_enabled=cfg.pocket_enabled):
        raise FileNotFoundError('Download do Piper não criou o par ONNX/JSON')


def voice_info(cfg: Config):
    from wyoming.info import Attribution, Info, TtsProgram, TtsVoice

    voices = [
        TtsVoice(name=voice.name, description=voice.description, version=None,
                 attribution=Attribution(name="Arandu TTS", url="https://github.com/OHF-Voice/piper1-gpl"),
                 installed=True, languages=[voice.language])
        for voice in catalogue(cfg.voices_dir, pocket_enabled=cfg.pocket_enabled).values()
    ]
    return Info(tts=[TtsProgram(name="arandu_tts", description="Arandu TTS (Piper e Pocket CPU / GPU opcional)",
                                attribution=Attribution(name="Arandu", url="https://github.com/OHF-Voice/piper1-gpl"),
                                installed=True, voices=voices, version=__version__,
                                # HA must finish synthesis before serving audio to satellites.
                                # Sentence-by-sentence inference can starve the ESPHome decoder
                                # midway through long replies on slower CPUs. Keep accepting the
                                # streaming protocol for existing clients, but advertise buffered
                                # synthesis so HA supplies a complete, uninterrupted recording.
                                supports_synthesize_streaming=False)])


class _State:
    """Estado de sessão de texto incremental de cada conexão Wyoming."""
    def __init__(self) -> None:
        self.buffer = SentenceBuffer()
        self.voice_name: str | None = None
        self.started = False
        self.total = 0
        self.rate: int | None = None


async def serve(cfg: Config) -> None:
    from wyoming.audio import AudioChunk, AudioStart, AudioStop
    from wyoming.error import Error
    from wyoming.info import Describe
    from wyoming.server import AsyncEventHandler, AsyncServer
    from wyoming.tts import Synthesize, SynthesizeStart, SynthesizeChunk, SynthesizeStop, SynthesizeStopped

    engine = SpeechEngine(cfg)
    ensure_default_model(cfg)
    if cfg.preload:
        available = catalogue(cfg.voices_dir, pocket_enabled=cfg.pocket_enabled)
        if cfg.default_voice in available:
            await asyncio.to_thread(engine.preload, available[cfg.default_voice])

    if cfg.pocket_preload and cfg.pocket_enabled:
        await asyncio.to_thread(engine.warm_pocket)

    class AranduHandler(AsyncEventHandler):
        def __init__(self, reader, writer):
            super().__init__(reader, writer)
            self.state: _State | None = None

        def _select(self, requested: str | None) -> VoiceSpec:
            name = requested or cfg.default_voice
            available = catalogue(cfg.voices_dir, pocket_enabled=cfg.pocket_enabled)
            if name not in available:
                raise ValueError(f"Voz {name!r} indisponível. Vozes: {', '.join(available)}")
            return available[name]

        async def _emit(self, text: str, voice_name: str | None, final: bool) -> None:
            if self.state is None:
                self.state = _State()
            voice = self._select(voice_name)
            text = normalize_text(text)
            if len(text) > cfg.max_chars:
                raise ValueError(f"Texto excede {cfg.max_chars} caracteres")
            start = time.perf_counter()
            first = True
            emitted = False

            async def forward(target: VoiceSpec) -> None:
                nonlocal first, emitted
                async for chunk in engine.stream(text, target):
                    if not self.state.started:
                        await self.write_event(AudioStart(rate=chunk.rate, width=chunk.width, channels=chunk.channels).event())
                        self.state.started = True
                        self.state.rate = chunk.rate
                    elif chunk.rate != self.state.rate:
                        raise ValueError("Não é possível misturar sample rates na mesma sessão")
                    await self.write_event(AudioChunk(audio=chunk.data, rate=chunk.rate,
                                                      width=chunk.width, channels=chunk.channels).event())
                    emitted = True
                    if first:
                        LOG.info("Primeiro chunk de %s em %.3fs", target.name, time.perf_counter() - start)
                        first = False

            if text:
                try:
                    await forward(voice)
                except Exception:
                    if emitted or not ((voice.backend == "remote" and cfg.remote_fallback) or
                                      (voice.backend == "pocket" and cfg.pocket_fallback)):
                        raise
                    backup = catalogue(cfg.voices_dir, pocket_enabled=cfg.pocket_enabled).get(cfg.fallback_voice)
                    if backup is None or backup.backend != "piper":
                        raise
                    LOG.warning("Motor %s indisponível: fallback Piper %s", voice.backend, backup.name)
                    await forward(backup)
            if final:
                # Um protocolo Wyoming de síntese vazia também deve encerrar a sessão.
                if not self.state.started:
                    await self.write_event(AudioStart(rate=22050, width=2, channels=1).event())
                await self.write_event(AudioStop().event())
                self.state = None

        async def handle_event(self, event) -> bool:
            try:
                if Describe.is_type(event.type):
                    await self.write_event(voice_info(cfg).event())
                    return True
                if Synthesize.is_type(event.type):
                    request = Synthesize.from_event(event)
                    self.state = _State()
                    name = request.voice.name if request.voice else None
                    await self._emit(request.text, name, final=True)
                    return True
                if SynthesizeStart.is_type(event.type):
                    request = SynthesizeStart.from_event(event)
                    self.state = _State()
                    self.state.voice_name = request.voice.name if request.voice else None
                    return True
                if SynthesizeChunk.is_type(event.type):
                    if self.state is None:
                        raise ValueError("synthesize-chunk sem synthesize-start")
                    request = SynthesizeChunk.from_event(event)
                    self.state.total += len(request.text)
                    if self.state.total > cfg.max_chars:
                        raise ValueError("Texto da sessão excede limite")
                    for sentence in self.state.buffer.push(request.text):
                        await self._emit(sentence, self.state.voice_name, final=False)
                    return True
                if SynthesizeStop.is_type(event.type):
                    if self.state is None:
                        raise ValueError("synthesize-stop sem synthesize-start")
                    final = self.state.buffer.finish()
                    await self._emit(final, self.state.voice_name, final=True)
                    await self.write_event(SynthesizeStopped().event())
                    return True
                return True
            except Exception as exc:
                LOG.exception("Falha na requisição Wyoming")
                self.state = None
                await self.write_event(Error(text=str(exc), code=type(exc).__name__).event())
                return False

    srv = AsyncServer.from_uri(cfg.uri)
    LOG.info("Arandu TTS %s em %s; voz padrão: %s", __version__, cfg.uri, cfg.default_voice)
    runner = None
    if cfg.web_enabled:
        from .web import start_web
        runner = await start_web(cfg, engine)
    try:
        await srv.run(AranduHandler)
    finally:
        if runner is not None:
            await runner.cleanup()


def main() -> None:
    parser = argparse.ArgumentParser(description="Arandu TTS Wyoming server")
    parser.add_argument("--uri", help="URI Wyoming (ex.: tcp://0.0.0.0:10212)")
    args = parser.parse_args()
    cfg = Config.from_env()
    if args.uri:
        from dataclasses import replace
        cfg = replace(cfg, uri=args.uri)
    logging.basicConfig(level=getattr(logging, cfg.log_level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    os.environ["OMP_NUM_THREADS"] = str(cfg.piper_threads)
    asyncio.run(serve(cfg))


if __name__ == "__main__":
    main()
