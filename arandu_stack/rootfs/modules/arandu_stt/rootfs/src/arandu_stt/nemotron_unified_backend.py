"""Experimental NVIDIA Nemotron Labs Unified ASR 0.6B backend (offline + buffered streaming)."""
from __future__ import annotations

from dataclasses import dataclass, field
import importlib.util
import os
from pathlib import Path
import tempfile
import threading
import time
import wave

import numpy as np

from stt_errors import AranduRuntimeUnavailableError

DEFAULT_NEMOTRON_PYTHON = Path('/opt/venvs/nemotron/bin/python')


def is_nemotron_runtime_available() -> bool:
    env_override = os.environ.get('ARANDU_NEMOTRON_PYTHON')
    if env_override and Path(env_override).is_file():
        return True
    if DEFAULT_NEMOTRON_PYTHON.is_file():
        return True
    return importlib.util.find_spec('nemo') is not None

STREAMING_PROFILES: dict[str, dict[str, int]] = {
    'precision': {'chunk_ms': 560, 'right_context_ms': 560, 'left_context_ms': 5600},
    'balanced': {'chunk_ms': 160, 'right_context_ms': 400, 'left_context_ms': 5600},
    'fast': {'chunk_ms': 80, 'right_context_ms': 160, 'left_context_ms': 5600},
    'experimental': {'chunk_ms': 80, 'right_context_ms': 80, 'left_context_ms': 5600},
}


@dataclass
class NemotronStreamSession:
    profile: str
    target_lang: str = 'pt'
    chunks: list[np.ndarray] = field(default_factory=list)
    total_samples: int = 0
    inference_ms: float = 0.0

    @property
    def algorithmic_latency_ms(self) -> int:
        spec = STREAMING_PROFILES[self.profile]
        return spec['chunk_ms'] + spec['right_context_ms']


class NemotronUnifiedBackend:
    def __init__(self, model_dir, threads=2, target_lang='pt', streaming_profile='balanced'):
        self.model_dir = Path(model_dir)
        self.threads = int(threads)
        self.target_lang = target_lang
        if streaming_profile not in STREAMING_PROFILES:
            raise ValueError(f'Perfil streaming Nemotron inválido: {streaming_profile}')
        self.streaming_profile = streaming_profile
        self._model = None
        self._lock = threading.Lock()
        self.load_ms = 0.0

    def load(self):
        nemo_path = self.model_dir / 'nemotron-labs-unified-asr-0.6b.nemo'
        if not nemo_path.is_file() or nemo_path.stat().st_size < 100_000_000:
            raise FileNotFoundError('Checkpoint nemotron-labs-unified-asr-0.6b.nemo ausente ou incompleto')
        try:
            import torch
            import nemo.collections.asr as nemo_asr  # type: ignore
        except ImportError as exc:
            raise AranduRuntimeUnavailableError(
                'NVIDIA NeMo/PyTorch indisponível no runtime STT padrão; requer ambiente Python isolado (/opt/venvs/nemotron)',
                stage='load',
            ) from exc

        start = time.perf_counter()
        torch.set_num_threads(max(1, min(4, self.threads)))
        self._model = nemo_asr.models.ASRModel.restore_from(restore_path=str(nemo_path), map_location='cpu')
        self._model.eval()
        self.load_ms = (time.perf_counter() - start) * 1000.0
        return self

    def _transcribe_wav(self, audio: np.ndarray, target_lang: str) -> str:
        pcm = np.clip(np.round(np.asarray(audio, dtype=np.float32) * 32768.0), -32768, 32767).astype('<i2')
        tmp = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
        tmp_path = Path(tmp.name)
        tmp.close()
        try:
            with wave.open(str(tmp_path), 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(pcm.tobytes())
            kwargs = {} if target_lang == 'auto' else {'target_lang': target_lang}
            out = self._model.transcribe([str(tmp_path)], **kwargs)
            if isinstance(out, (list, tuple)) and out:
                first = out[0]
                return (getattr(first, 'text', None) or str(first or '')).strip()
            return ''
        finally:
            tmp_path.unlink(missing_ok=True)

    def recognize(self, audio, target_lang: str | None = None):
        if self._model is None:
            raise RuntimeError('Nemotron Unified ASR não carregado')
        lang = target_lang or self.target_lang
        with self._lock:
            start = time.perf_counter()
            text = self._transcribe_wav(audio, lang)
            return text, (time.perf_counter() - start) * 1000.0

    def create_stream(self, profile: str | None = None, target_lang: str | None = None) -> NemotronStreamSession:
        prof = profile or self.streaming_profile
        if prof not in STREAMING_PROFILES:
            raise ValueError(f'Perfil streaming Nemotron inválido: {prof}')
        return NemotronStreamSession(profile=prof, target_lang=target_lang or self.target_lang)

    def feed_chunk(self, session: NemotronStreamSession, chunk: np.ndarray) -> dict:
        arr = np.asarray(chunk, dtype=np.float32).reshape(-1)
        if len(arr):
            session.chunks.append(arr)
            session.total_samples += len(arr)
        spec = STREAMING_PROFILES[session.profile]
        min_samples = int(16 * spec['chunk_ms'])
        return {
            'buffered_samples': session.total_samples,
            'ready_for_step': session.total_samples >= min_samples,
            'algorithmic_latency_ms': session.algorithmic_latency_ms,
        }

    def finish_stream(self, session: NemotronStreamSession) -> dict:
        if not session.chunks:
            return {'text': '', 'inference_ms': 0.0, 'algorithmic_latency_ms': session.algorithmic_latency_ms}
        full_audio = np.concatenate(session.chunks)
        text, inf_ms = self.recognize(full_audio, target_lang=session.target_lang)
        session.inference_ms = inf_ms
        return {
            'text': text,
            'inference_ms': inf_ms,
            'algorithmic_latency_ms': session.algorithmic_latency_ms,
            'profile': session.profile,
        }

    def close(self):
        with self._lock:
            self._model = None
