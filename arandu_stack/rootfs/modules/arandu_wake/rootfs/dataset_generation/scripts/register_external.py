"""Registra negativos/RIR legais por referência, sem baixar, copiar ou usar no gerador."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wakegen.common import ROOT, atomic_json, now
from wakegen.texts import contains_wake


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("registry", type=Path)
    args = parser.parse_args()
    registry = json.loads(args.registry.read_text(encoding="utf-8-sig"))
    registered = []
    for item in registry["assets"]:
        for key in ("path", "kind", "license", "source_url", "sha256", "split"):
            if not item.get(key):
                raise ValueError(f"Campo obrigatório: {key}")
        if item["split"] not in {"train", "validation", "test"}:
            raise ValueError("Split externo inválido")
        path = Path(item["path"]).resolve()
        if sha256(path) != item["sha256"]:
            raise ValueError(f"Checksum divergente: {path.name}")
        if contains_wake(item.get("transcript", "")):
            raise ValueError("Transcrição negativa externa contém Arandu")
        if item["kind"] == "openwakeword_embeddings":
            import numpy as np
            array = np.load(path, mmap_mode="r", allow_pickle=False)
            if array.ndim < 2 or not item.get("feature_format") or not item.get("feature_model_revision"):
                raise ValueError("Embeddings exigem formato/revisão explícitos e array sem pickle")
            details = {"shape": list(array.shape), "dtype": str(array.dtype), "engine": "openwakeword", "native_training_compatibility": "requires_future_adapter_validation"}
        else:
            info = sf.info(path)
            details = {"sample_rate": info.samplerate, "channels": info.channels, "duration_seconds": info.duration}
            if item["kind"] == "negative_speech" and (info.samplerate != 16000 or info.channels != 1 or info.subtype != "PCM_16"):
                raise ValueError("Fala negativa externa deve estar em PCM16 mono 16 kHz")
        registered.append(dict(item, path=str(path), details=details))
    # Registro versionado, sem sobrescrever registros aprovados anteriores.
    stamp = now().replace(":", "-")
    destination = ROOT / "data/manifests/external" / f"assets-{stamp}.json"
    atomic_json(destination, {"registered_at": now(), "assets": registered,
                             "license_review": "declared_by_operator_not_legal_certification", "used_for_generation": False})
    print(str(destination))


if __name__ == "__main__":
    main()
