"""Persistent per-session WebRTC Audio Processing (HPF, AEC3, NS, AGC2, Resampling)."""
from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any

import numpy as np

PRESETS: dict[str, dict[str, Any]] = {
    'original': {
        'vad_enabled': False,
        'high_pass_filter': False,
        'noise_suppression': False,
        'ns_level': 1,
        'auto_gain_control': False,
        'agc_gain_db': 0.0,
        'echo_cancellation': False,
    },
    'vad': {
        'vad_enabled': True,
        'high_pass_filter': False,
        'noise_suppression': False,
        'ns_level': 1,
        'auto_gain_control': False,
        'agc_gain_db': 0.0,
        'echo_cancellation': False,
    },
    'balanced': {
        'vad_enabled': True,
        'high_pass_filter': True,
        'noise_suppression': True,
        'ns_level': 1,
        'auto_gain_control': False,
        'agc_gain_db': 0.0,
        'echo_cancellation': False,
    },
    'custom': {},
}


@dataclass
class AudioProcessorConfig:
    sample_rate: int = 16000
    num_channels: int = 1
    high_pass_filter: bool = False
    noise_suppression: bool = False
    ns_level: int = 1
    auto_gain_control: bool = False
    agc_gain_db: float = 0.0
    echo_cancellation: bool = False
    stream_delay_ms: int = 0

    def __post_init__(self) -> None:
        if self.sample_rate not in (8000, 16000, 32000, 44100, 48000):
            raise ValueError(f'Taxa de amostragem não suportada: {self.sample_rate}')
        if self.num_channels not in (1, 2):
            raise ValueError(f'Número de canais inválido: {self.num_channels}')
        if not 0 <= int(self.ns_level) <= 3:
            raise ValueError('ns_level deve ficar entre 0 e 3')
        if not -30.0 <= float(self.agc_gain_db) <= 30.0:
            raise ValueError('agc_gain_db deve ficar entre -30 e 30')
        if not 0 <= int(self.stream_delay_ms) <= 500:
            raise ValueError('stream_delay_ms deve ficar entre 0 e 500')


def _to_mono_float32(audio: np.ndarray, channels: int) -> np.ndarray:
    arr = np.asarray(audio, dtype=np.float32)
    if channels == 2:
        if arr.ndim == 2 and arr.shape[1] == 2:
            arr = arr.mean(axis=1, dtype=np.float32)
        elif arr.ndim == 1 and len(arr) >= 2:
            even = len(arr) - (len(arr) % 2)
            arr = arr[:even].reshape(-1, 2).mean(axis=1, dtype=np.float32)
    return np.ascontiguousarray(arr, dtype=np.float32)


def resample_to_16khz(audio: np.ndarray, src_rate: int) -> np.ndarray:
    if src_rate == 16000 or len(audio) == 0:
        return np.ascontiguousarray(audio, dtype=np.float32)
    dst_len = max(1, int(round(len(audio) * 16000.0 / float(src_rate))))
    src_idx = np.linspace(0.0, len(audio) - 1, num=len(audio), dtype=np.float64)
    dst_idx = np.linspace(0.0, len(audio) - 1, num=dst_len, dtype=np.float64)
    return np.ascontiguousarray(np.interp(dst_idx, src_idx, audio.astype(np.float64)).astype(np.float32))


def webrtc_available() -> bool:
    try:
        import pywebrtc_audio  # noqa: F401
        return True
    except ImportError:
        return False


class AudioProcessor:
    """Session-scoped WebRTC AudioProcessor + 16 kHz mono normalizer."""

    def __init__(self, config: AudioProcessorConfig | None = None, **kwargs: Any) -> None:
        self.config = config or AudioProcessorConfig(**kwargs)
        self._native: Any = None
        self._residual = np.empty(0, dtype=np.int16)
        self._aec_reference_seen = False
        self._closed = False
        self._init_error: str | None = None
        self._webrtc_requested = any((
            self.config.high_pass_filter,
            self.config.noise_suppression,
            self.config.auto_gain_control,
            self.config.echo_cancellation,
        ))
        if self._webrtc_requested:
            self._init_native()

    def _init_native(self) -> None:
        try:
            from pywebrtc_audio import AudioProcessor as NativeAP  # type: ignore
        except ImportError as exc:
            self._init_error = f'pywebrtc_audio indisponível: {exc}'
            self._native = None
            return
        self._native = NativeAP(
            sample_rate=16000,
            num_channels=1,
            echo_cancellation=bool(self.config.echo_cancellation),
            noise_suppression=bool(self.config.noise_suppression),
            high_pass_filter=bool(self.config.high_pass_filter),
            auto_gain_control=bool(self.config.auto_gain_control),
            ns_level=int(self.config.ns_level),
            agc_gain_db=float(self.config.agc_gain_db),
            stream_delay_ms=int(self.config.stream_delay_ms),
        )

    def aec_diagnostics(self) -> dict[str, Any]:
        return {
            'requested': bool(self.config.echo_cancellation),
            'webrtc_available': self._native is not None,
            'reference_available': bool(self._aec_reference_seen),
            'active': bool(self.config.echo_cancellation and self._native is not None and self._aec_reference_seen),
            'stream_delay_ms': int(self.config.stream_delay_ms),
            'reason': (
                None if not self.config.echo_cancellation
                else (self._init_error if self._native is None
                      else (None if self._aec_reference_seen else 'Sem referência acústica far-end real nesta fonte'))
            ),
        }

    def process_pcm16(
        self,
        pcm: bytes,
        *,
        sample_rate: int | None = None,
        channels: int | None = None,
        far_end_pcm: bytes | None = None,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        if self._closed:
            raise RuntimeError('AudioProcessor encerrado')
        started = time.perf_counter()
        rate = sample_rate or self.config.sample_rate
        ch = channels or self.config.num_channels
        if len(pcm) % 2:
            raise ValueError('PCM16 com número ímpar de bytes')
        raw_i16 = np.frombuffer(pcm, dtype='<i2')
        f32 = _to_mono_float32(raw_i16.astype(np.float32) / 32768.0, ch)
        f32_16k = resample_to_16khz(f32, rate)

        i16_16k = np.clip(np.round(f32_16k * 32768.0), -32768, 32767).astype(np.int16)
        if far_end_pcm and self.config.echo_cancellation and self._native is not None:
            self._aec_reference_seen = True

        aec_active = bool(self.config.echo_cancellation and self._native is not None and far_end_pcm)
        webrtc_applied = False
        if self._native is not None and len(i16_16k) > 0:
            combined = np.concatenate((self._residual, i16_16k)) if len(self._residual) else i16_16k
            frame_samples = 160  # 10 ms at 16 kHz
            n_frames = len(combined) // frame_samples
            out_chunks = []
            far_i16 = None
            if aec_active and far_end_pcm:
                far_arr = np.frombuffer(far_end_pcm, dtype='<i2')
                far_f32 = resample_to_16khz(_to_mono_float32(far_arr.astype(np.float32) / 32768.0, ch), rate)
                far_i16 = np.clip(np.round(far_f32 * 32768.0), -32768, 32767).astype(np.int16)
            for idx in range(n_frames):
                frame = combined[idx * frame_samples : (idx + 1) * frame_samples]
                if aec_active and far_i16 is not None and hasattr(self._native, 'process_reverse_stream'):
                    f_start = idx * frame_samples
                    far_frame = far_i16[f_start : f_start + frame_samples]
                    if len(far_frame) == frame_samples:
                        self._native.process_reverse_stream(far_frame.tobytes())
                processed = self._native.process_stream(frame.tobytes())
                out_chunks.append(np.frombuffer(processed, dtype='<i2'))
            self._residual = combined[n_frames * frame_samples :].copy()
            if out_chunks:
                out_i16 = np.concatenate(out_chunks)
                f32_16k = out_i16.astype(np.float32) / 32768.0
                webrtc_applied = True
            else:
                f32_16k = np.empty(0, dtype=np.float32)

        elapsed_ms = (time.perf_counter() - started) * 1000.0
        stats = {
            'preprocess_ms': round(elapsed_ms, 3),
            'webrtc_requested': self._webrtc_requested,
            'webrtc_available': self._native is not None,
            'webrtc_applied': webrtc_applied,
            'webrtc_error': self._init_error,
            'aec': self.aec_diagnostics(),
            'aec_active': aec_active,
            'resampled_from_hz': rate if rate != 16000 else None,
        }
        return f32_16k, stats

    def flush(self) -> np.ndarray:
        if self._closed or len(self._residual) == 0:
            return np.empty(0, dtype=np.float32)
        pad = 160 - len(self._residual)
        padded = np.concatenate((self._residual, np.zeros(pad, dtype=np.int16)))
        self._residual = np.empty(0, dtype=np.int16)
        if self._native is not None:
            out = np.frombuffer(self._native.process_stream(padded.tobytes()), dtype='<i2')[: 160 - pad]
            return out.astype(np.float32) / 32768.0
        return padded[: 160 - pad].astype(np.float32) / 32768.0

    def close(self) -> None:
        self._closed = True
        self._residual = np.empty(0, dtype=np.int16)
        if self._native is not None and hasattr(self._native, 'close'):
            try:
                self._native.close()
            except Exception:
                pass
        self._native = None
