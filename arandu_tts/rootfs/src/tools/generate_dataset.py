#!/usr/bin/env python3
"""Gera dataset Piper (WAV+metadata.csv) com voz OmniVoice já clonada."""
from __future__ import annotations
import argparse
import csv
import re
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description="Gerar frases com voz clonada em GPU")
    p.add_argument("--profile", required=True, type=Path)
    p.add_argument("--sentences", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--device", default="cuda:0")
    p.add_argument("--limit", type=int, default=0)
    args = p.parse_args()
    if not args.profile.is_file() or not args.sentences.is_file():
        p.error("Perfil e arquivo de frases precisam existir")
    import soundfile as sf
    from scipy.signal import resample_poly
    import torch
    from omnivoice import OmniVoice, VoiceClonePrompt
    model = OmniVoice.from_pretrained("k2-fsa/OmniVoice", device_map=args.device,
                                      dtype=torch.float16 if args.device.startswith("cuda") else torch.float32)
    prompt = VoiceClonePrompt.load(str(args.profile))
    audio_dir = args.out / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    lines = [line.strip() for line in args.sentences.read_text(encoding="utf-8-sig").splitlines()]
    lines = [line for line in lines if line and not line.startswith("#")]
    if args.limit:
        lines = lines[:args.limit]
    with (args.out / "metadata.csv").open("w", encoding="utf-8", newline="") as csv_file:
        for idx, line in enumerate(lines, 1):
            if "|" in line or len(line) > 300:
                raise ValueError(f"Frase {idx} contém '|' ou passa de 300 caracteres")
            out = f"utt{idx:05d}.wav"
            audio = model.generate(text=line, voice_clone_prompt=prompt)
            # OmniVoice produz 24 kHz; Piper treina por padrão em 22.050 Hz.
            samples_22050 = resample_poly(audio[0], 147, 160)
            sf.write(audio_dir / out, samples_22050, 22050, subtype="PCM_16")
            csv_file.write(f"{out}|{line}\n")
            print(f"{idx}/{len(lines)} {out}", flush=True)
    print("Dataset gerado:", args.out)


if __name__ == "__main__":
    main()
