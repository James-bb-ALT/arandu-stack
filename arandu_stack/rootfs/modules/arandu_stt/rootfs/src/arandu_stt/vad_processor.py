"""Silero VAD v6.2 ONNX CPU processor with recurrent state, pre-roll ring buffer and pause tolerance."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import tempfile
import time
from typing import Any
import urllib.request

import numpy as np

SILERO_VAD_VERSION = 'v6.2'
SILERO_VAD_URL = 'https://raw.githubusercontent.com/snakers4/silero-vad/v6.2/src/silero_vad/data/silero_vad.onnx'
SILERO_VAD_SHA256 = '1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3'
WINDOW_SAMPLES = 512  # 32 ms at 16 kHz
CONTEXT_SAMPLES = 64


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def ensure_silero_vad(dest_dir: Path, *, allow_download: bool = False) -> Path:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / 'silero_vad.onnx'
    if target.is_file() and _sha256_file(target) == SILERO_VAD_SHA256:
        return target
    if not allow_download:
        raise FileNotFoundError('Silero VAD ONNX v6.2 não instalado em ' + str(target))
    with tempfile.NamedTemporaryFile(dir=dest_dir, suffix='.download', delete=False) as tmp:
        tmp_path = Path(tmp.name)
        req = urllib.request.Request(SILERO_VAD_URL, headers={'User-Agent': 'Arandu-STT/4.1.0'})
        with urllib.request.urlopen(req, timeout=30) as resp:
            while chunk := resp.read(262144):
                tmp.write(chunk)
        tmp.flush()
        os.fsync(tmp.fileno())
    try:
        if _sha256_file(tmp_path) != SILERO_VAD_SHA256:
            raise ValueError('SHA-256 inválido para Silero VAD v6.2')
        os.replace(tmp_path, target)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return target


@dataclass
class VadConfig:
    sample_rate: int = 16000
    threshold: float = 0.50
    min_speech_ms: int = 180
    min_silence_ms: int = 450
    pre_roll_ms: int = 300
    post_roll_ms: int = 200
    max_utterance_s: float = 35.0

    def __post_init__(self) -> None:
        if self.sample_rate != 16000:
            raise ValueError('Silero VAD exige 16000 Hz')
        if not 0.05 <= float(self.threshold) <= 0.95:
            raise ValueError('threshold do VAD deve ficar entre 0.05 e 0.95')
        if not 50 <= int(self.min_speech_ms) <= 2000:
            raise ValueError('min_speech_ms inválido')
        if not 100 <= int(self.min_silence_ms) <= 3000:
            raise ValueError('min_silence_ms inválido')
        if not 0 <= int(self.pre_roll_ms) <= 1000:
            raise ValueError('pre_roll_ms inválido')
        if not 0 <= int(self.post_roll_ms) <= 1000:
            raise ValueError('post_roll_ms inválido')


@dataclass
class VadResult:
    audio: np.ndarray
    speech_detected: bool
    speech_ms: float
    vad_ms: float
    end_of_speech_ms: float | None
    windows_total: int
    windows_speech: int
    max_probability: float
    segments: list[tuple[int, int]] = field(default_factory=list)


class SileroVADProcessor:
    """Session-isolated Silero VAD v6.2 ONNX runner with recurrent state and ring buffer."""

    def __init__(self, model_path: Path | None = None, config: VadConfig | None = None, **kwargs: Any) -> None:
        self.config = config or VadConfig(**kwargs)
        self.model_path = Path(model_path) if model_path is not None else None
        self._session: Any = None
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, CONTEXT_SAMPLES), dtype=np.float32)
        self._pre_roll_windows = max(1, int(round(self.config.pre_roll_ms / 32.0)))
        self._ring: deque[np.ndarray] = deque(maxlen=self._pre_roll_windows)
        self.reset()

    def load(self) -> 'SileroVADProcessor':
        if self._session is not None:
            return self
        if self.model_path is None or not self.model_path.is_file():
            raise FileNotFoundError('Silero VAD ONNX ausente')
        if _sha256_file(self.model_path) != SILERO_VAD_SHA256:
            raise ValueError('Silero VAD ONNX com SHA-256 divergente')
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        self._session = ort.InferenceSession(str(self.model_path), sess_options=opts, providers=['CPUExecutionProvider'])
        return self

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, CONTEXT_SAMPLES), dtype=np.float32)
        self._ring.clear()

    def _infer_window(self, window: np.ndarray) -> float:
        self.load()
        x = np.asarray(window, dtype=np.float32).reshape(1, WINDOW_SAMPLES)
        inp = np.concatenate([self._context, x], axis=1)
        out, state = self._session.run(
            None,
            {'input': inp, 'state': self._state, 'sr': np.array(self.config.sample_rate, dtype=np.int64)},
        )
        self._state = state
        self._context = inp[:, -CONTEXT_SAMPLES:]
        return float(np.asarray(out).reshape(-1)[0])

    def process_audio(self, audio: np.ndarray) -> VadResult:
        started = time.perf_counter()
        self.reset()
        samples = np.asarray(audio, dtype=np.float32).reshape(-1)
        if len(samples) == 0:
            return VadResult(np.empty(0, dtype=np.float32), False, 0.0, 0.0, None, 0, 0, 0.0)

        min_speech_win = max(1, int(round(self.config.min_speech_ms / 32.0)))
        min_silence_win = max(1, int(round(self.config.min_silence_ms / 32.0)))
        post_roll_win = max(0, int(round(self.config.post_roll_ms / 32.0)))

        n_win = (len(samples) + WINDOW_SAMPLES - 1) // WINDOW_SAMPLES
        probs: list[float] = []
        windows: list[np.ndarray] = []
        for idx in range(n_win):
            chunk = samples[idx * WINDOW_SAMPLES : (idx + 1) * WINDOW_SAMPLES]
            if len(chunk) < WINDOW_SAMPLES:
                chunk = np.pad(chunk, (0, WINDOW_SAMPLES - len(chunk)))
            windows.append(chunk)
            probs.append(self._infer_window(chunk))

        thr = float(self.config.threshold)
        in_speech = False
        speech_run = 0
        silence_run = 0
        segments: list[tuple[int, int]] = []
        seg_start = 0

        for idx, prob in enumerate(probs):
            if prob >= thr:
                speech_run += 1
                silence_run = 0
                if not in_speech and speech_run >= min_speech_win:
                    in_speech = True
                    seg_start = max(0, idx - speech_run + 1 - self._pre_roll_windows)
            else:
                if in_speech:
                    silence_run += 1
                    if silence_run >= min_silence_win:
                        seg_end = min(n_win, idx - silence_run + 1 + post_roll_win)
                        segments.append((seg_start, seg_end))
                        in_speech = False
                        speech_run = 0
                        silence_run = 0
                else:
                    speech_run = 0

        if in_speech:
            segments.append((seg_start, n_win))

        if not segments:
            elapsed = (time.perf_counter() - started) * 1000.0
            return VadResult(
                audio=np.empty(0, dtype=np.float32),
                speech_detected=False,
                speech_ms=0.0,
                vad_ms=round(elapsed, 3),
                end_of_speech_ms=None,
                windows_total=n_win,
                windows_speech=sum(1 for p in probs if p >= thr),
                max_probability=max(probs) if probs else 0.0,
                segments=[],
            )

        merged: list[tuple[int, int]] = []
        for start_w, end_w in segments:
            if merged and start_w <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end_w))
            else:
                merged.append((start_w, end_w))

        out_parts = []
        sample_segments: list[tuple[int, int]] = []
        for start_w, end_w in merged:
            s_idx = start_w * WINDOW_SAMPLES
            e_idx = min(len(samples), end_w * WINDOW_SAMPLES)
            sample_segments.append((s_idx, e_idx))
            out_parts.append(samples[s_idx:e_idx])

        speech_audio = np.concatenate(out_parts) if out_parts else np.empty(0, dtype=np.float32)
        last_end_ms = sample_segments[-1][1] / 16.0 if sample_segments else None
        elapsed = (time.perf_counter() - started) * 1000.0
        return VadResult(
            audio=speech_audio,
            speech_detected=len(speech_audio) > 0,
            speech_ms=round(len(speech_audio) / 16.0, 2),
            vad_ms=round(elapsed, 3),
            end_of_speech_ms=last_end_ms,
            windows_total=n_win,
            windows_speech=sum(1 for p in probs if p >= thr),
            max_probability=max(probs) if probs else 0.0,
            segments=sample_segments,
        )

    def close(self) -> None:
        self._session = None
        self.reset()
