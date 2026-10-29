"""QC acústico conservador; não certifica pronúncia humana nem identidade."""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal
from scipy.fft import dct


def resample(audio, source_rate, target_rate):
    divisor = math.gcd(int(source_rate), int(target_rate))
    return signal.resample_poly(np.asarray(audio, dtype=np.float32), int(target_rate) // divisor, int(source_rate) // divisor).astype(np.float32)


def activity_bounds(audio, sample_rate):
    x = np.asarray(audio, dtype=np.float32).reshape(-1)
    hop = max(1, int(sample_rate * 0.01))
    frames = np.pad(x, (0, (-len(x)) % hop)).reshape(-1, hop)
    rms = np.sqrt(np.mean(frames * frames, axis=1) + 1e-12)
    threshold = max(10 ** (-48 / 20), float(np.max(rms)) * 0.06)
    active = np.flatnonzero(rms > threshold)
    if not len(active):
        return None, None, 1.0
    return max(0, int(active[0] * hop)), min(len(x), int((active[-1] + 1) * hop)), 1 - len(active) / len(rms)


def inspect_audio(audio, rate, config, kind="word"):
    x = np.asarray(audio)
    reasons, warnings = [], []
    if x.ndim != 1 or not len(x) or not np.isfinite(x).all():
        return {"valid": False, "reasons": ["empty_nonmono_or_nonfinite"]}
    duration = len(x) / rate
    peak = float(np.max(np.abs(x)))
    rms = float(np.sqrt(np.mean(x.astype(np.float64) ** 2) + 1e-15))
    clipped = float(np.mean(np.abs(x) >= 0.999))
    start, end, silence = activity_bounds(x, rate)
    limits = config["audio"]
    max_duration = limits["max_word_seconds"] if kind == "word" else (22 if kind == "reference" else limits["max_general_seconds"])
    min_duration = limits["min_word_seconds"] if kind == "word" else 0.3
    if not min_duration <= duration <= max_duration:
        reasons.append("implausible_duration")
    if 20 * math.log10(rms) < limits["minimum_rms_dbfs"]:
        reasons.append("too_quiet")
    if clipped > limits["maximum_clipped_fraction"]:
        reasons.append("severe_clipping")
    if silence > limits["maximum_silence_fraction"] or start is None:
        reasons.append("excessive_silence_or_no_activity")
    # Quatro blocos de 0.5 s quase idênticos detectam loops, sem confundir pitch periódico.
    size = int(rate * 0.5)
    identical = 0
    for index in range(size, len(x) - size + 1, size):
        left, right = x[index - size:index], x[index:index + size]
        if np.std(left) > 0.004 and np.std(right) > 0.004 and np.corrcoef(left, right)[0, 1] > 0.9995:
            identical += 1
    if identical >= 3:
        reasons.append("repetitive_audio_loop")
    edge = max(1, int(rate * 0.005))
    if float(np.sqrt(np.mean(x[-edge:] ** 2))) > max(0.04, rms * 0.8):
        warnings.append("possible_truncation_review")
    return {"valid": not reasons, "reasons": reasons, "warnings": warnings,
            "duration_seconds": duration, "peak": peak, "rms_dbfs": 20 * math.log10(rms),
            "clipped_fraction": clipped, "silence_fraction": silence,
            "activity_start_seconds": start / rate if start is not None else None,
            "activity_end_seconds": end / rate if end is not None else None,
            "vocal_presence": "energy_activity_only_not_verified_speech"}


def canonical_clip(audio, rate, config, rng):
    x = resample(audio, rate, 16000)
    start, end, _ = activity_bounds(x, 16000)
    if start is None:
        raise ValueError("Sem atividade no áudio")
    # Margens preservam ataque e final; não são alinhamento fonético forçado.
    start, end = max(0, start - 960), min(len(x), end + 1600)
    clean = x[start:end].copy()
    content_hash = hashlib.sha256(np.round(np.clip(clean, -1, 1) * 32767).astype("<i2").tobytes()).hexdigest()
    speed = rng.uniform(*config["generation"]["speed_range"])
    x = signal.resample_poly(clean, 1000, round(speed * 1000)).astype(np.float32)
    rms = float(np.sqrt(np.mean(x * x) + 1e-12))
    gain = min(3.0, 0.10 / rms, 0.94 / max(float(np.max(np.abs(x))), 1e-8))
    x *= gain
    lead = rng.uniform(*config["generation"]["leading_silence_seconds"])
    tail = rng.uniform(*config["generation"]["trailing_silence_seconds"])
    result = np.pad(x, (int(lead * 16000), int(tail * 16000)))
    return result, {"resampling": "scipy.resample_poly", "native_rate": rate, "sample_rate": 16000,
                    "source_trim_seconds": [start / 16000, end / 16000], "speed_factor": speed,
                    "gain": gain, "leading_silence_seconds": lead, "trailing_silence_seconds": tail,
                    "speech_region_seconds": [lead, lead + len(x) / 16000],
                    "content_hash_before_padding_and_speed": content_hash,
                    "speed_is_augmentation_not_new_speaker": True}


def write_wav_atomic(path, audio, sample_rate):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"Não sobrescrever áudio existente: {path}")
    temp = path.with_name(path.name + f".{os.getpid()}.part")
    sf.write(temp, np.asarray(audio, dtype=np.float32), sample_rate, format="WAV", subtype="PCM_16")
    with temp.open("rb+") as source:
        os.fsync(source.fileno())
    info = sf.info(temp)
    if info.samplerate != sample_rate or info.channels != 1 or info.subtype != "PCM_16" or info.frames == 0:
        raise ValueError("WAV temporário inválido")
    os.replace(temp, path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def acoustic_features(audio, sample_rate):
    x = resample(audio, sample_rate, 16000)
    _, _, spectrum = signal.stft(x, fs=16000, nperseg=400, noverlap=240, boundary=None)
    cepstrum = dct(np.log(np.abs(spectrum) + 1e-6), axis=0, norm="ortho")[:20]
    return {"log_spectrum_cepstral_mean": np.mean(cepstrum, axis=1).tolist(),
            "log_spectrum_cepstral_std": np.std(cepstrum, axis=1).tolist(),
            "duration_seconds": len(audio) / sample_rate,
            "interpretation": "descriptive_acoustic_features_not_proof_of_distinct_identity"}


def augment(audio, sample_rate, seed, noise=None, impulse_response=None, snr_db=15,
            microphone_profile=None, mulaw_codec=False):
    """Transformação lazy; fontes externas precisam de licença no registro de ativos."""
    rng = np.random.default_rng(seed)
    x = np.asarray(audio, dtype=np.float32).copy()
    # Perfis aproximados de banda; não são medições de um dispositivo específico.
    bands = {"tablet_approx": (120, 6500), "phone_approx": (180, 7000), "esp_satellite_approx": (100, 6000)}
    if microphone_profile is not None:
        if microphone_profile not in bands:
            raise ValueError("Perfil aproximado de microfone desconhecido")
        low, high = bands[microphone_profile]
        high = min(high, sample_rate * 0.45)
        filters = signal.butter(3, [low, high], btype="bandpass", fs=sample_rate, output="sos")
        x = signal.sosfilt(filters, x).astype(np.float32)
    if impulse_response is not None:
        response = np.asarray(impulse_response, dtype=np.float32)
        response /= max(float(np.sqrt(np.sum(response ** 2))), 1e-8)
        x = signal.fftconvolve(x, response)[:len(x)]
    if noise is not None:
        noise = np.asarray(noise, dtype=np.float32)
        if not len(noise):
            raise ValueError("Ruído vazio")
        repeats = math.ceil(len(x) / len(noise)) + 1
        noise = np.tile(noise, repeats)
        offset = int(rng.integers(0, len(noise) - len(x) + 1))
        noise = noise[offset:offset + len(x)]
        noise *= float(np.sqrt(np.mean(x * x) + 1e-12) / np.sqrt(np.mean(noise * noise) + 1e-12)) / (10 ** (snr_db / 20))
        x += noise
    x = x / max(1.0, float(np.max(np.abs(x))) / 0.98)
    if mulaw_codec:
        encoded = np.sign(x) * np.log1p(255 * np.abs(x)) / np.log1p(255)
        quantized = np.round((encoded + 1) * 127.5) / 127.5 - 1
        x = np.sign(quantized) * np.expm1(np.abs(quantized) * np.log1p(255)) / 255
    return x.astype(np.float32)
