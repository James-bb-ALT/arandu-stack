"""Explicit non-production ONNX artifacts for inspecting a trained checkpoint."""
from pathlib import Path

from .common import digest, write_json, write_text
from .models import load_nemo


def export_for_review(checkpoint: Path, output: Path) -> dict:
    import onnx
    model, _ = load_nemo(str(checkpoint))
    model.eval().to("cpu")
    model.set_export_config({"decoder_type": "ctc"})
    output.mkdir(parents=True, exist_ok=True)
    path = output / "model.onnx"
    model.export(str(path), onnx_opset_version=17)
    graph = onnx.load(str(path))
    normalize = str(model.cfg.preprocessor.normalize)
    onnx.helper.set_model_props(graph, {"model_type": "EncDecHybridRNNTCTCBPEModel", "version": "1",
                                      "model_author": "NeMo", "subsampling_factor": "8",
                                      "normalize_type": "" if normalize == "NA" else normalize,
                                      "vocab_size": str(len(model.ctc_decoder.vocabulary)),
                                      "comment": "REVIEW ONLY: production quality gate not passed"})
    onnx.checker.check_model(graph)
    onnx.save(graph, str(path))
    vocabulary = list(model.ctc_decoder.vocabulary)
    write_text(output / "tokens.txt", "".join(f"{token} {i}\n" for i, token in enumerate(vocabulary)) + f"<blk> {len(vocabulary)}\n")
    result = {"ready_for_manual_deployment": False, "scope": "review_only", "model_sha256": digest(path),
              "checkpoint_sha256": digest(checkpoint), "production_replaced": False}
    write_json(output / "review_export.json", result)
    return result
