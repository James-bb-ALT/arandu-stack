#!/usr/bin/env python3
"""Container entrypoint: relocate, fine-tune, evaluate, export for local comparison."""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
import traceback

from stt_pipeline.cached_training import relocate_dataset, train_cached
from stt_pipeline.common import canonical, digest, read_json, read_jsonl, write_json
from stt_pipeline.evaluation import alignment, evaluate, predict
from stt_pipeline.exporting import export_model


def word_diagnostics(records, baseline, candidate):
    result = {}
    for split in ("train", "validation", "test"):
        subset = [row for row in records if row.get("split") == split]
        result[split] = {"scope": "training_diagnostic" if split == "train" else "heldout_human"}
        for word in ("ventilador", "jaime"):
            counts = {name: {"occurrences": 0, "correct": 0} for name in ("baseline", "candidate")}
            for name, predictions in (("baseline", baseline), ("candidate", candidate)):
                for row in subset:
                    for operation in alignment(canonical(row["text"]).split(), canonical(predictions[row["id"]]).split()):
                        if operation["reference"] == word:
                            counts[name]["occurrences"] += 1
                            counts[name]["correct"] += operation["operation"] == "equal"
            result[split][word] = counts
    return result


def run(repo: Path, output: Path, *, dataset_source=None, checkpoint=None, baseline_runtime=None,
        epochs=80, learning_rate=.0003, patience=12):
    output.mkdir(parents=True, exist_ok=True)
    dataset = output / "dataset"
    continued = checkpoint is not None
    relocate_dataset(dataset_source or repo / "dataset", dataset)
    checkpoint = checkpoint or repo / "models" / "trainable_original" / "stt_pt_fastconformer_hybrid_large_pc.nemo"
    baseline_runtime = baseline_runtime or repo / "models" / "original_int8"
    training = train_cached(dataset, output, checkpoint, epochs=epochs, batch_size=8,
                            learning_rate=learning_rate, patience=patience, resume=(output / "last.pt").exists(),
                            save_final_epoch=continued)
    progress_path = output / "progress.json"

    def status(phase, message):
        current = read_json(progress_path)
        write_json(progress_path, {**current, "phase": phase, "message": message})

    review_checkpoint = output / "best.nemo"
    if continued and not training["head_changed"]:
        # Keep the validation-selected parent intact, but also measure and
        # export the actual newly trained weights as an experimental artifact.
        review_checkpoint = output / "final_epoch.nemo"
    status("evaluating", "Comparando original e adaptado no mesmo teste independente")
    comparison = evaluate(dataset, output / "evaluation", model=str(review_checkpoint),
                          baseline_model=str(checkpoint if continued else baseline_runtime), device="cpu", threads=2)
    write_json(output / "comparison.json", comparison)
    if comparison["comparison"]["improved"] and not continued:
        status("exporting", "Exportando CTC e verificando FP32/INT8 no runtime Arandu")
        export = export_model(output / "best.nemo", dataset, output / "export", output / "comparison.json")
    else:
        # Comparative review is still useful if quality does not improve. The
        # production release gate remains closed; we export a review-only graph.
        status("exporting", "Criando FP32 de revisão para comparar com a versão anterior")
        from stt_pipeline.preview_export import export_for_review
        export = export_for_review(review_checkpoint, output / "review_model")
    result = {"training": training, "comparison": comparison["comparison"], "export": export,
              "production_replaced": False, "initial_checkpoint": str(checkpoint),
              "initial_checkpoint_sha256": digest(checkpoint), "continued_training": continued}
    result["review_checkpoint"] = str(review_checkpoint)
    result["validation_selected_checkpoint"] = str(output / "best.nemo")
    result["validation_improved"] = training["best_epoch"] > 0
    status("evaluating", "Medindo os dois artefatos finais no mesmo runtime CPU")
    if export.get("scope") == "review_only":
        candidate = output / "review_model"
    else:
        candidate = output / "export" / ("int8" if export["ready_for_manual_deployment"] else "fp32")
    runtime_comparison = evaluate(dataset, output / "runtime_evaluation", model=str(candidate),
                                  baseline_model=str(baseline_runtime), threads=2)
    write_json(output / "comparison_runtime.json", runtime_comparison)
    result["runtime_comparison"] = runtime_comparison["comparison"]
    result["selected_runtime_format"] = "INT8" if candidate.name == "int8" else "FP32"
    if continued:
        status("evaluating", "Verificando ventilador e Jaime, com treino e conjuntos independentes separados")
        validation_comparison = evaluate(dataset, output / "runtime_validation", model=str(candidate),
                                         baseline_model=str(baseline_runtime), split="validation", threads=2)
        result["runtime_validation_comparison"] = validation_comparison["comparison"]
        records = [row for row in read_jsonl(dataset / "metadata.jsonl")
                   if set(canonical(row["text"]).split()) & {"jaime", "ventilador"}]
        previous_predictions, _, _ = predict(records, str(baseline_runtime), threads=2)
        current_predictions, _, _ = predict(records, str(candidate), threads=2)
        target_report = word_diagnostics(records, previous_predictions, current_predictions)
        target_report["utterances"] = [{"id": row["id"], "text": row["text"], "split": row["split"],
                                         "synthetic": row.get("synthetic", False),
                                         "baseline": previous_predictions[row["id"]],
                                         "candidate": current_predictions[row["id"]]} for row in records]
        write_json(output / "target_words.json", target_report)
        result["target_words"] = {key: value for key, value in target_report.items() if key != "utterances"}
    write_json(output / "experiment.json", result)
    status("completed", "Treinamento e comparação concluídos. Modelo de produção preservado.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("/workspace"))
    parser.add_argument("--output", type=Path, default=Path("/experiment"))
    parser.add_argument("--dataset-source", type=Path)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--baseline-runtime", type=Path)
    parser.add_argument("--epochs", type=int, default=80)
    parser.add_argument("--learning-rate", type=float, default=.0003)
    parser.add_argument("--patience", type=int, default=12)
    args = parser.parse_args()
    try:
        run(args.repo, args.output, dataset_source=args.dataset_source, checkpoint=args.checkpoint,
            baseline_runtime=args.baseline_runtime, epochs=args.epochs,
            learning_rate=args.learning_rate, patience=args.patience)
    except Exception as exc:
        args.output.mkdir(parents=True, exist_ok=True)
        previous = read_json(args.output / "progress.json") if (args.output / "progress.json").exists() else {}
        write_json(args.output / "progress.json", {**previous, "phase": "failed", "message": str(exc)})
        (args.output / "failure.txt").write_text(traceback.format_exc(), encoding="utf-8")
        raise
