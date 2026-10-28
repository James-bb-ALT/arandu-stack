from __future__ import annotations

import importlib.metadata
from pathlib import Path

from .common import digest

UPSTREAM = "nvidia/stt_pt_fastconformer_hybrid_large_pc"
REVISION = "036da25fa32e3f11c11105448482845950840685"
CHECKPOINT_FILENAME = "stt_pt_fastconformer_hybrid_large_pc.nemo"


def checkpoint_path(value: str, revision: str = REVISION) -> Path:
    if value == UPSTREAM:
        from huggingface_hub import hf_hub_download
        return Path(hf_hub_download(UPSTREAM, CHECKPOINT_FILENAME, revision=revision))
    path = Path(value).resolve()
    if not path.is_file() or path.suffix != ".nemo":
        raise ValueError("Use o checkpoint treinável .nemo ou o nome upstream documentado; ONNX INT8 não é treinável.")
    return path


def load_nemo(value: str, revision: str = REVISION, device="cpu"):
    import torch
    from nemo.collections.asr.models import EncDecHybridRNNTCTCBPEModel
    path = checkpoint_path(value, revision)
    model = EncDecHybridRNNTCTCBPEModel.restore_from(str(path), map_location=torch.device(device))
    if type(model).__name__ != "EncDecHybridRNNTCTCBPEModel" or not hasattr(model, "ctc_decoder"):
        raise ValueError("Checkpoint incompatível com FastConformer híbrido BPE/CTC")
    cfg = model.cfg
    if int(cfg.preprocessor.sample_rate) != 16000 or int(cfg.preprocessor.features) != 80:
        raise ValueError("Checkpoint exige parâmetros diferentes do runtime Arandu: esperado 16 kHz/80 features")
    if int(cfg.encoder.subsampling_factor) != 8:
        raise ValueError("Esperado FastConformer com subsampling_factor=8")
    if model.tokenizer.vocab_size != 128:
        raise ValueError("Esperado tokenizador original SentencePiece de 128 tokens; não substitua o vocabulário.")
    model.change_decoding_strategy(decoder_type="ctc")
    return model, path


def package_versions() -> dict:
    result = {}
    for package in ("nemo_toolkit", "torch", "lightning", "huggingface_hub", "sherpa-onnx", "onnx", "onnxruntime", "numpy"):
        try:
            result[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            pass
    return result


def model_identity(value: str, revision: str = REVISION) -> dict:
    path = Path(value)
    if path.is_dir():
        return {"type": "sherpa_onnx_ctc", "files": {p.name: digest(p) for p in sorted(path.iterdir())
                                                 if p.name in ("tokens.txt", "model.onnx", "model.int8.onnx")}}
    checkpoint = checkpoint_path(value, revision)
    return {"type": "nemo_ctc", "checkpoint": str(checkpoint), "sha256": digest(checkpoint),
            "upstream": UPSTREAM if value == UPSTREAM else None, "revision": revision if value == UPSTREAM else None}
