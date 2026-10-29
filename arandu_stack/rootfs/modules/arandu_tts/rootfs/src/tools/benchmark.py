#!/usr/bin/env python3
"""Benchmark local com cargas de frases e percentis."""
from __future__ import annotations
import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path
from arandu_tts.config import Config
from arandu_tts.engine import SpeechEngine
from arandu_tts.voices import catalogue

DEFAULT_PHRASES = [
    "A luz da cozinha foi acesa.", "A temperatura do escritório é de vinte e seis graus.",
    "Desliguei os ventiladores do escritório de Jaime.",
    "Encontrei a playlist solicitada no Music Assistant.",
    "O timer foi configurado para dez minutos.",
]


def percentile(xs, p):
    seq = sorted(xs)
    return seq[min(len(seq) - 1, round((len(seq) - 1) * p))]


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--voices-dir", type=Path, required=True)
    p.add_argument("--voice", required=True)
    p.add_argument("--repeat", type=int, default=3)
    p.add_argument("--out", type=Path)
    args = p.parse_args()
    voices = catalogue(args.voices_dir)
    if args.voice not in voices:
        p.error(f"Voz não encontrada; disponíveis: {list(voices)}")
    cfg = Config(voices_dir=args.voices_dir)
    engine = SpeechEngine(cfg)
    await asyncio.to_thread(engine.preload, voices[args.voice])
    rows = []
    for _ in range(args.repeat):
        for phrase in DEFAULT_PHRASES:
            start = time.perf_counter()
            first = None
            frames = 0
            sample_rate = None
            async for chunk in engine.stream(phrase, voices[args.voice]):
                if first is None:
                    first = time.perf_counter() - start
                frames += len(chunk.data) // (chunk.width * chunk.channels)
                sample_rate = chunk.rate
            elapsed = time.perf_counter() - start
            duration = frames / sample_rate if sample_rate else 0
            rows.append({"phrase": phrase, "first_chunk_s": first, "total_s": elapsed,
                         "audio_s": duration, "rtf": elapsed / duration if duration else None})
    results = {
        "voice": args.voice, "runs": len(rows),
        "first_chunk_p50_s": percentile([r["first_chunk_s"] for r in rows], .5),
        "first_chunk_p95_s": percentile([r["first_chunk_s"] for r in rows], .95),
        "rtf_p50": percentile([r["rtf"] for r in rows], .5), "rows": rows,
    }
    body = json.dumps(results, ensure_ascii=False, indent=2)
    print(body)
    if args.out:
        args.out.write_text(body, encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
