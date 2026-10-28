#!/usr/bin/env python3
"""Normaliza áudios com ffmpeg e transcrições (stem.txt) para treinamento Piper."""
from __future__ import annotations
import argparse
import csv
import subprocess
import wave
from pathlib import Path

AUDIO_EXT = {".wav", ".mp3", ".flac", ".m4a", ".ogg", ".mp4", ".opus", ".aac"}


def main():
    p = argparse.ArgumentParser(description="Dataset mono PCM 22050 Hz para Piper")
    p.add_argument("--input", type=Path, required=True, help="Origem com audio.ext e audio.txt")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--strict", action="store_true", help="Reprova audios sem TXT ao invés de ignorar")
    args = p.parse_args()
    if not args.input.is_dir():
        p.error("Diretório de entrada inexistente")
    out_audio = args.out / "audio"
    out_audio.mkdir(parents=True, exist_ok=True)
    audio_files = sorted(f for f in args.input.iterdir() if f.is_file() and f.suffix.lower() in AUDIO_EXT)
    if not audio_files:
        p.error("Nenhum áudio encontrado")
    count = 0
    with (args.out / "metadata.csv").open("w", encoding="utf-8", newline="") as output:
        for src in audio_files:
            txt = src.with_suffix(".txt")
            if not txt.is_file():
                if args.strict:
                    raise FileNotFoundError(f"Transcrição não encontrada: {txt}")
                print("Ignorando sem TXT:", src.name)
                continue
            transcript = " ".join(txt.read_text(encoding="utf-8-sig").split())
            if not transcript or "|" in transcript:
                raise ValueError(f"Transcrição vazia ou com separador '|' no arquivo: {txt}")
            count += 1
            name = f"utt{count:05d}.wav"
            result = subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-i", str(src), "-vn", "-ac", "1", "-ar", "22050", "-c:a", "pcm_s16le", str(out_audio / name),
            ], capture_output=True, text=True)
            if result.returncode:
                raise RuntimeError(f"ffmpeg falhou para {src.name}: {result.stderr[-1000:]}")
            with wave.open(str(out_audio / name), "rb") as wav:
                duration = wav.getnframes() / wav.getframerate()
            if not (0.5 <= duration <= 20):
                print(f"Atenção: {src.name}: {duration:.1f} s (ideal 0,5–20 s)")
            output.write(f"{name}|{transcript}\n")
    if count == 0:
        raise RuntimeError("Nenhuma dupla áudio/transcrição válida")
    print(f"Preparado: {count} registros em {args.out}")


if __name__ == "__main__":
    main()
