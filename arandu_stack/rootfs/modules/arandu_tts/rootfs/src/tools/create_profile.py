#!/usr/bin/env python3
"""Cria perfil reutilizável OmniVoice .pt a partir de WAV e transcrição exata."""
from __future__ import annotations
import argparse
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description="Criar perfil de voz OmniVoice")
    p.add_argument("--audio", type=Path, required=True)
    p.add_argument("--text", type=Path, required=True, help="TXT contendo transcrição exata do áudio")
    p.add_argument("--out", type=Path, required=True, help="Perfis .pt do OmniVoice")
    p.add_argument("--device", default="cuda:0")
    args = p.parse_args()
    if not args.audio.is_file() or not args.text.is_file():
        p.error("Arquivos de áudio e transcrição devem existir")
    transcript = args.text.read_text(encoding="utf-8").strip()
    if not transcript:
        p.error("Transcrição vazia")
    import torch
    from omnivoice import OmniVoice
    model = OmniVoice.from_pretrained("k2-fsa/OmniVoice", device_map=args.device,
                                      dtype=torch.float16 if args.device.startswith("cuda") else torch.float32)
    prompt = model.create_voice_clone_prompt(ref_audio=str(args.audio), ref_text=transcript)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    prompt.save(str(args.out))
    print("Perfil salvo em", args.out, "— não é um modelo Piper")


if __name__ == "__main__":
    main()
