from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import shutil
import tempfile
import wave

from .common import digest, run


class AudioTools:
    def __init__(self, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe"):
        self.ffmpeg = shutil.which(ffmpeg)
        self.ffprobe = shutil.which(ffprobe)
        if not self.ffmpeg or not self.ffprobe:
            raise ValueError("Instale FFmpeg e FFprobe ou forneça --ffmpeg e --ffprobe.")

    def probe(self, path: Path, stream_index: int | None = None) -> dict:
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError("Arquivo ausente ou vazio")
        data = json.loads(run([self.ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)]))
        streams = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
        if stream_index is not None:
            streams = [s for s in streams if s["index"] == stream_index]
        elif len(streams) > 1:
            defaults = [s for s in streams if s.get("disposition", {}).get("default") == 1]
            streams = defaults if len(defaults) == 1 else streams
        if len(streams) != 1:
            raise ValueError(f"Esperado um fluxo de áudio inequívoco; encontrados {len(streams)}. Use stream_index no mapeamento.")
        stream = streams[0]
        duration = stream.get("duration", data.get("format", {}).get("duration"))
        return {"stream_index": stream["index"], "codec": stream.get("codec_name"),
                "format": data.get("format", {}).get("format_name"),
                "source_sample_rate": stream.get("sample_rate"), "source_channels": stream.get("channels"),
                "source_duration": duration}

    def decode_check(self, path: Path) -> None:
        run([self.ffmpeg, "-v", "error", "-xerror", "-nostdin", "-i", str(path),
             "-map", "0:a:0", "-f", "null", "-"], timeout=300)

    def convert(self, source: Path, target: Path, info: dict, sample_rate: int,
                source_hash: str, cache: dict) -> dict:
        import os
        signature = f"pcm-v1:{sample_rate}:{info['stream_index']}:{source_hash}"
        if target.is_file() and cache.get("signature") == signature and cache.get("wav_sha256") == digest(target):
            details = wav_details(target, sample_rate)
            self.decode_check(target)
            return {**details, "signature": signature, "wav_sha256": cache["wav_sha256"], "cached": True}
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(suffix=".wav", dir=target.parent)
        os.close(fd)
        temporary = Path(name)
        try:
            run([self.ffmpeg, "-v", "error", "-xerror", "-nostdin", "-y", "-i", str(source),
                 "-map", f"0:{info['stream_index']}", "-vn", "-ac", "1", "-ar", str(sample_rate),
                 "-c:a", "pcm_s16le", "-f", "wav", str(temporary)], timeout=300)
            details = wav_details(temporary, sample_rate)
            self.decode_check(temporary)
            os.replace(temporary, target)
            return {**details, "signature": signature, "wav_sha256": digest(target), "cached": False}
        finally:
            temporary.unlink(missing_ok=True)


def wav_details(path: Path, sample_rate: int) -> dict:
    h = hashlib.sha256()
    peak = 0
    with wave.open(str(path), "rb") as f:
        if f.getnchannels() != 1 or f.getsampwidth() != 2 or f.getframerate() != sample_rate or f.getcomptype() != "NONE":
            raise ValueError(f"Parâmetros WAV incorretos: {path}")
        frames = f.getnframes()
        if frames <= 0:
            raise ValueError("Áudio sem amostras")
        read_frames = 0
        while True:
            block = f.readframes(65536)
            if not block:
                break
            read_frames += len(block) // 2
            h.update(block)
            # PCM little-endian on both Windows and Linux.
            import array
            import sys
            values = array.array("h", block)
            if sys.byteorder != "little":
                values.byteswap()
            peak = max(peak, max(abs(v) for v in values))
        if read_frames != frames:
            raise ValueError("WAV truncado")
    duration = frames / sample_rate
    if not math.isfinite(duration) or peak == 0:
        raise ValueError("Áudio vazio ou completamente silencioso")
    return {"duration": duration, "sample_rate": sample_rate, "channels": 1,
            "sample_width": 2, "pcm_sha256": h.hexdigest(), "peak": peak}
