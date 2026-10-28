from __future__ import annotations

from pathlib import Path
import math

from .common import digest, read_json, read_jsonl, write_json, write_text
from .dataset import validate
from .evaluation import evaluate
from .models import REVISION, UPSTREAM, load_nemo, package_versions

PRODUCTION_INT8_SHA256 = "cc4135abd8cf485f2588fa584a9fd2e006e26ab5cae23703e3db37c9151f75b5"
PRODUCTION_TOKENS_SHA256 = "c79cd022b6b04e090901e1acce380ad9476bb6a3694112199b835256bb079b9b"


def check_export_gate(checkpoint: Path, dataset: Path, report_path: Path) -> dict:
    report = read_json(report_path)
    if report.get("split") != "test" or not report.get("inference_verified") or not report.get("comparison", {}).get("improved"):
        raise ValueError("Exportação exige comparação por inferência no teste, com WER menor e CER sem regressão.")
    if report["candidate"]["identity"].get("sha256") != digest(checkpoint):
        raise ValueError("Checkpoint não corresponde ao candidato avaliado")
    baseline = report["baseline"]["identity"]
    original = ((baseline.get("upstream") == UPSTREAM and baseline.get("revision") == REVISION)
                or (baseline.get("files", {}).get("model.int8.onnx") == PRODUCTION_INT8_SHA256
                    and baseline.get("files", {}).get("tokens.txt") == PRODUCTION_TOKENS_SHA256))
    if not original:
        raise ValueError("Baseline deve ser o checkpoint NVIDIA original ou o INT8 registrado no Arandu.")
    manifest = dataset / "manifests" / "test.jsonl"
    if report["manifest_sha256"] != digest(manifest):
        raise ValueError("Manifest de teste mudou desde a avaliação")
    fingerprints = {r["id"]: digest(Path(r["audio_filepath"])) for r in read_jsonl(manifest)}
    if report["audio_fingerprints"] != fingerprints:
        raise ValueError("Áudios de teste mudaram desde a avaliação")
    return report


def export_model(checkpoint: Path, dataset: Path, output: Path, comparison: Path, *,
                 max_wer_regression=0.005, max_cer_regression=0.005, threads=2,
                 ffmpeg="ffmpeg", ffprobe="ffprobe") -> dict:
    if any(not math.isfinite(x) or x < 0 for x in (max_wer_regression, max_cer_regression)):
        raise ValueError("Tolerância de regressão não pode ser negativa")
    checkpoint, output = checkpoint.resolve(), output.resolve()
    evidence = check_export_gate(checkpoint, dataset, comparison)
    if not validate(dataset, ffmpeg=ffmpeg, ffprobe=ffprobe)["ready"]:
        raise ValueError("Dataset reprovado")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Destino de exportação deve estar vazio; nunca sobrescreva produção.")
    import onnx
    from onnxruntime.quantization import QuantType, quantize_dynamic
    from omegaconf import OmegaConf
    model, _ = load_nemo(str(checkpoint))
    model.to("cpu").eval()
    model.preprocessor.featurizer.dither = 0.0
    model.preprocessor.featurizer.pad_to = 0
    vocabulary = list(model.joint.vocabulary)
    if vocabulary != list(model.ctc_decoder.vocabulary) or len(vocabulary) != 128:
        raise ValueError("Vocabulário CTC/RNNT inconsistente")
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "export.json", {"ready_for_manual_deployment": False, "status": "export_in_progress"})
    fp32_dir, int8_dir = output / "fp32", output / "int8"
    fp32_dir.mkdir()
    int8_dir.mkdir()
    # NeMo's native CTC exporter produces external-feature inputs, matching sherpa-onnx.
    model.set_export_config({"decoder_type": "ctc"})
    filename = fp32_dir / "model.onnx"
    model.export(str(filename), onnx_opset_version=17)
    graph = onnx.load(str(filename))
    input_names = [i.name for i in graph.graph.input]
    if len(input_names) != 2 or len(graph.graph.output) < 1:
        raise ValueError("Assinatura ONNX incompatível com NeMo CTC offline")
    input_dims = graph.graph.input[0].type.tensor_type.shape.dim
    if len(input_dims) != 3 or input_dims[1].dim_value != 80:
        raise ValueError("ONNX deve receber [batch, 80, frames]")
    normalize = str(model.cfg.preprocessor.normalize)
    if normalize == "NA":
        normalize = ""
    if normalize not in ("", "per_feature", "all_features"):
        raise ValueError(f"Normalização incompatível: {normalize}")
    metadata = {"model_type": "EncDecHybridRNNTCTCBPEModel", "version": "1", "model_author": "NeMo",
                "subsampling_factor": "8", "normalize_type": normalize, "vocab_size": str(len(vocabulary)),
                "url": f"https://huggingface.co/{UPSTREAM}", "comment": "Arandu fine-tuned CTC branch; manual deployment only"}
    # Retain other exporter metadata and add the required sherpa fields.
    existing = {p.key: p.value for p in graph.metadata_props}
    onnx.helper.set_model_props(graph, {**existing, **metadata})
    onnx.checker.check_model(graph)
    onnx.save(graph, str(filename))
    token_text = "".join(f"{token} {i}\n" for i, token in enumerate(vocabulary)) + f"<blk> {len(vocabulary)}\n"
    write_text(fp32_dir / "tokens.txt", token_text)
    write_text(int8_dir / "tokens.txt", token_text)
    write_text(output / "model_config.yaml", OmegaConf.to_yaml(model.cfg))
    quantize_dynamic(str(filename), str(int8_dir / "model.int8.onnx"), weight_type=QuantType.QUInt8)
    onnx.checker.check_model(onnx.load(str(int8_dir / "model.int8.onnx")))
    del model, graph
    import gc
    gc.collect()
    # Both artifacts are decoded with the production recognizer API on the same held-out test set.
    quantization = evaluate(dataset, output / "quantization_evaluation", model=str(int8_dir), baseline_model=str(fp32_dir),
                            device="cpu", threads=threads, ffmpeg=ffmpeg, ffprobe=ffprobe)
    fp32_metrics, int8_metrics = quantization["baseline"]["metrics"], quantization["candidate"]["metrics"]
    source_metrics = evidence["candidate"]["metrics"]
    export_delta = {"wer": fp32_metrics["wer"] - source_metrics["wer"], "cer": fp32_metrics["cer"] - source_metrics["cer"]}
    accepted = (quantization["comparison"]["wer_delta"] <= max_wer_regression
                and quantization["comparison"]["cer_delta"] <= max_cer_regression
                and export_delta["wer"] <= max_wer_regression and export_delta["cer"] <= max_cer_regression
                and int8_metrics["wer"] < evidence["baseline"]["metrics"]["wer"]
                and int8_metrics["cer"] <= evidence["baseline"]["metrics"]["cer"])
    result = {"ready_for_manual_deployment": accepted, "status": "validated" if accepted else "rejected_accuracy_regression",
              "checkpoint_sha256": digest(checkpoint), "comparison_sha256": digest(comparison),
              "manifest_sha256": quantization["manifest_sha256"], "versions": package_versions(),
              "export_metrics_delta_from_nemo": export_delta, "quantization_delta": quantization["comparison"],
              "regression_tolerances": {"wer": max_wer_regression, "cer": max_cer_regression},
              "fp32": {"metrics": fp32_metrics, "performance": quantization["baseline"]["performance"],
                       "bytes": filename.stat().st_size, "sha256": digest(filename)},
              "int8": {"metrics": int8_metrics, "performance": quantization["candidate"]["performance"],
                       "bytes": (int8_dir / "model.int8.onnx").stat().st_size, "sha256": digest(int8_dir / "model.int8.onnx")},
              "tokens_sha256": digest(int8_dir / "tokens.txt"), "deployment_directory": str(int8_dir),
              "production_replaced": False,
              "runtime_verification": "OfflineRecognizer.from_nemo_ctc, CPU, 16 kHz, 80 features, greedy_search; Wyoming/HAOS live deployment still required"}
    write_json(output / "export.json", result)
    return result
