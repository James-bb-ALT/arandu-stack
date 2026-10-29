#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

REPO = "csukuangfj/sherpa-onnx-nemo-stt_pt_fastconformer_hybrid_large_pc-int8"
MODEL = "model.int8.onnx"
UPSTREAM = "nvidia/stt_pt_fastconformer_hybrid_large_pc"
ADAPTED_VERSION = "arandu-ctc-20261014-continued-fp32"
ADAPTED_URL = "https://github.com/jaimevictor/arandu-stack/releases/download/stt-model-v2.2.0"
ADAPTED_HASHES = {
    "model.onnx": "68fff73a46efa49914df12fa74e83e3e36803ab80a8fbea86b6f7c8684e3c112",
    "tokens.txt": "c79cd022b6b04e090901e1acce380ad9476bb6a3694112199b835256bb079b9b",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_original_model(dest: Path) -> dict:
    from huggingface_hub import HfApi, hf_hub_download

    dest.mkdir(parents=True, exist_ok=True)
    model = dest / MODEL
    tokens = dest / "tokens.txt"
    manifest = dest / "manifest.json"
    if model.is_file() and model.stat().st_size > 10_000_000 and tokens.is_file() and tokens.stat().st_size > 100:
        if manifest.is_file():
            return json.loads(manifest.read_text(encoding="utf-8"))
        return {"status": "cached", "repo": REPO}

    print(f"[MODEL] Baixando FastConformer INT8 de {REPO}", flush=True)
    revision = HfApi().model_info(repo_id=REPO).sha
    if not revision:
        raise RuntimeError("Não foi possível resolver a revisão do modelo")

    for filename in (MODEL, "tokens.txt"):
        path = Path(hf_hub_download(repo_id=REPO, filename=filename, revision=revision, local_dir=str(dest)))
        if not path.is_file():
            raise RuntimeError(f"Download incompleto: {path}")

    if model.stat().st_size <= 10_000_000 or tokens.stat().st_size <= 100:
        raise RuntimeError("Arquivos do modelo parecem incompletos")

    data = {
        "upstream": UPSTREAM,
        "upstream_license": "CC-BY-NC-4.0",
        "converted_repo": REPO,
        "revision": revision,
        "model_filename": MODEL,
        "model_bytes": model.stat().st_size,
        "model_sha256": sha256(model),
        "tokens_sha256": sha256(tokens),
    }
    manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[MODEL] OK SHA256={data['model_sha256']}", flush=True)
    return data


def ensure_model(dest: Path, variant: str = "adapted") -> dict:
    """Install into a versioned directory; never overwrite the original model."""
    if variant == "original":
        return ensure_original_model(dest)
    if variant != "adapted":
        raise ValueError(f"Modelo desconhecido: {variant}")
    dest.mkdir(parents=True, exist_ok=True)
    for filename, expected in ADAPTED_HASHES.items():
        target = dest / filename
        if target.is_file() and sha256(target) == expected:
            continue
        print(f"[MODEL] Baixando {ADAPTED_VERSION}: {filename}", flush=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=dest, suffix=".download", delete=False) as output:
                temporary = Path(output.name)
                request = urllib.request.Request(f"{ADAPTED_URL}/{filename}", headers={"User-Agent": "Arandu-STT/2.2.0"})
                try:
                    response = urllib.request.urlopen(request, timeout=120)
                except urllib.error.HTTPError as exc:
                    raise RuntimeError(f"Download STT indisponível (HTTP {exc.code}): {ADAPTED_URL}/{filename}. Verifique o acesso à release pública do modelo.") from exc
                with response:
                    while chunk := response.read(4 * 1024 * 1024):
                        output.write(chunk)
            if sha256(temporary) != expected:
                raise RuntimeError(f"SHA256 inválido: {filename}; modelo não instalado")
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    data = {
        "version": ADAPTED_VERSION,
        "upstream": UPSTREAM,
        "upstream_license": "CC-BY-NC-4.0",
        "source": ADAPTED_URL,
        "model_filename": "model.onnx",
        "model_bytes": (dest / "model.onnx").stat().st_size,
        "model_sha256": ADAPTED_HASHES["model.onnx"],
        "tokens_sha256": ADAPTED_HASHES["tokens.txt"],
    }
    manifest = dest / "manifest.json"
    staged_manifest = dest / "manifest.json.tmp"
    staged_manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(staged_manifest, manifest)
    return data
