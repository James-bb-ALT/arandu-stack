"""Exporta índices adaptáveis aos motores. Não calcula features nem treina modelos."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wakegen.common import ROOT, atomic_json, now
from wakegen.store import Store


def export(engine, destination, include_review=False):
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / f"{engine}-audio-index.jsonl"
    if target.exists():
        raise FileExistsError("Índice existente preservado. Escolha outra pasta de exportação")
    store = Store()
    seen = {}
    counts = {}
    try:
        temporary = target.with_name(target.name + ".part")
        with temporary.open("w", encoding="utf-8") as output:
            for record in store.records():
                speaker, split = record["speaker_id"], record["split"]
                if speaker in seen and seen[speaker] != split:
                    raise ValueError("Vazamento de identidade entre splits")
                seen[speaker] = split
                if record["validation"]["status"] == "review" and not include_review:
                    continue
                path = ROOT / record["path"]
                if not path.is_file():
                    raise FileNotFoundError(path)
                item = {"id": record["sample_id"], "audio_path": str(path), "label": 1 if record["category"] == "positive" else 0,
                        "category": record["category"], "split": split, "speaker_id": speaker, "sample_rate": 16000,
                        "wake_word_interval_seconds": record["wake_word_interval_seconds"], "validation": record["validation"]["status"],
                        "source_manifest": str(ROOT / "data/manifests/samples.jsonl")}
                output.write(json.dumps(item, ensure_ascii=False) + "\n")
                counts[split] = counts.get(split, 0) + 1
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
        atomic_json(destination / "export.json", {"engine": engine, "created_at": now(), "counts": counts,
            "includes_unreviewed_candidates": include_review, "format": "PCM16 mono 16kHz audio index, not native model features",
            "next_preprocessing": "openWakeWord audio embedding pipeline" if engine == "openwakeword" else "microWakeWord microfrontend spectrogram pipeline",
            "external_asset_registries": [str(p) for p in (ROOT / "data/manifests/external").glob("*.json")],
            "trained": False, "model_exported": False})
    finally:
        store.close()
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=["openwakeword", "microwakeword"], required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--include-review", action="store_true")
    args = parser.parse_args()
    print(export(args.engine, args.out, args.include_review))
