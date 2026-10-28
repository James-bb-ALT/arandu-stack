"""Reutiliza snapshots existentes somente para leitura; baixa ausentes no cache próprio."""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wakegen.common import ROOT, atomic_json, load_config


def usable(path, kind):
    path = Path(path)
    if kind == "asr":
        return (path / "model.bin").is_file() and (path / "tokenizer.json").is_file()
    return (path / "config.json").is_file() and any(path.glob("*.safetensors")) and (path / "speech_tokenizer/config.json").is_file()


def main():
    own_cache = ROOT / "hf-cache/hub"
    os.environ["HF_HOME"] = str(ROOT / "hf-cache")
    from huggingface_hub import HfApi, snapshot_download
    config = load_config()
    registry_file = ROOT / "state/models.json"
    registry = json.loads(registry_file.read_text()) if registry_file.exists() else {}
    candidates = [
        Path.home() / ".cache/huggingface/hub",
        ROOT.parent / "Arandu_TTS_Dataset_Generation/hf-cache/hub",
        ROOT.parent / "Arandu TTS/hf-cache/hub",
    ]
    for name, repo in config["models"].items():
        if name == "asr" and not config["asr"]["enabled"]:
            continue
        entry = registry.get(name)
        if entry and usable(entry["path"], name):
            print(f"{name}: snapshot registrado disponível", flush=True)
            continue
        # Não escreve refs, locks ou blobs em caches externos.
        reused = None
        for cache in candidates:
            snapshots = cache / ("models--" + repo.replace("/", "--")) / "snapshots"
            for snapshot in sorted(snapshots.glob("*"), reverse=True):
                if usable(snapshot, name):
                    reused = snapshot
                    break
            if reused:
                break
        if reused:
            registry[name] = {"repo": repo, "revision": reused.name, "path": str(reused.resolve()), "source": "existing_snapshot_read_only"}
        else:
            revision = HfApi().model_info(repo).sha
            print(f"{name}: baixando {repo}@{revision} somente para {own_cache}", flush=True)
            snapshot = snapshot_download(repo, revision=revision, cache_dir=str(own_cache),
                allow_patterns=["*.json", "*.txt", "*.safetensors", "*.model", "*.bin", "*.npz"], max_workers=2)
            if not usable(snapshot, name):
                raise RuntimeError(f"Snapshot incompleto: {name}")
            registry[name] = {"repo": repo, "revision": revision, "path": str(Path(snapshot).resolve()), "source": "own_cache"}
        atomic_json(registry_file, registry)
        print(f"{name}: pronto", flush=True)
    print("Modelos preparados. Nenhuma síntese ou alteração em processo existente.", flush=True)


if __name__ == "__main__":
    main()
