from __future__ import annotations

from collections import Counter, defaultdict
import gc
import logging
from pathlib import Path
import statistics
import time
import wave

from .common import canonical, digest, read_jsonl, write_json, write_jsonl
from .dataset import validate
from .models import REVISION, model_identity, load_nemo, package_versions


def alignment(reference: list[str], hypothesis: list[str]) -> list[dict]:
    """Levenshtein alignment, retaining substitutions, deletions and insertions."""
    matrix = [[0] * (len(hypothesis) + 1) for _ in range(len(reference) + 1)]
    for i in range(len(reference) + 1):
        matrix[i][0] = i
    for j in range(len(hypothesis) + 1):
        matrix[0][j] = j
    for i, ref in enumerate(reference, 1):
        for j, hyp in enumerate(hypothesis, 1):
            matrix[i][j] = min(matrix[i - 1][j] + 1, matrix[i][j - 1] + 1,
                               matrix[i - 1][j - 1] + (ref != hyp))
    operations = []
    i, j = len(reference), len(hypothesis)
    while i or j:
        if i and j and matrix[i][j] == matrix[i - 1][j - 1] + (reference[i - 1] != hypothesis[j - 1]):
            op = "equal" if reference[i - 1] == hypothesis[j - 1] else "substitution"
            operations.append({"operation": op, "reference": reference[i - 1], "hypothesis": hypothesis[j - 1]})
            i, j = i - 1, j - 1
        elif i and matrix[i][j] == matrix[i - 1][j] + 1:
            operations.append({"operation": "deletion", "reference": reference[i - 1], "hypothesis": ""})
            i -= 1
        else:
            operations.append({"operation": "insertion", "reference": "", "hypothesis": hypothesis[j - 1]})
            j -= 1
    return list(reversed(operations))


def error_counts(reference: str, hypothesis: str, normalized=True) -> dict:
    ref, hyp = (canonical(reference), canonical(hypothesis)) if normalized else (reference, hypothesis)
    words = alignment(ref.split(), hyp.split())
    characters = alignment(list(ref), list(hyp))
    counts = Counter(op["operation"] for op in words if op["operation"] != "equal")
    return {"word_errors": sum(counts.values()), "reference_words": len(ref.split()),
            "character_errors": sum(op["operation"] != "equal" for op in characters), "reference_characters": len(ref),
            "substitutions": counts["substitution"], "deletions": counts["deletion"], "insertions": counts["insertion"],
            "word_errors_detail": [op for op in words if op["operation"] != "equal"]}


def aggregate(rows: list[dict]) -> dict:
    fields = ("word_errors", "reference_words", "character_errors", "reference_characters", "substitutions", "deletions", "insertions")
    totals = {field: sum(row[field] for row in rows) for field in fields}
    totals.update(examples=len(rows), wer=totals["word_errors"] / totals["reference_words"] if totals["reference_words"] else None,
                  cer=totals["character_errors"] / totals["reference_characters"] if totals["reference_characters"] else None)
    return totals


def score_predictions(records: list[dict], predictions: dict[str, str]) -> dict:
    expected = {r["id"] for r in records}
    if set(predictions) != expected or any(not isinstance(v, str) for v in predictions.values()):
        raise ValueError("Predições devem conter exatamente os IDs do conjunto, com text string (inclusive vazio).")
    rows, strict_rows = [], []
    by_speaker = defaultdict(list)
    confusions = Counter()
    for record in records:
        row = {"id": record["id"], "speaker": record["speaker"], "reference": record["text"],
               "hypothesis": predictions[record["id"]], **error_counts(record["text"], predictions[record["id"]])}
        rows.append(row)
        by_speaker[record["speaker"]].append(row)
        strict_rows.append(error_counts(record["text"], predictions[record["id"]], normalized=False))
        for op in row["word_errors_detail"]:
            confusions[(op["operation"], op["reference"], op["hypothesis"])] += 1
    return {"overall": aggregate(rows), "verbatim": aggregate(strict_rows), "per_speaker": {s: aggregate(r) for s, r in sorted(by_speaker.items())},
            "utterances": rows, "word_confusions": [{"operation": k[0], "reference": k[1], "hypothesis": k[2], "count": v}
                                                     for k, v in confusions.most_common()]}


def read_predictions(path: Path) -> dict[str, str]:
    rows = read_jsonl(path)
    if any(set(r) != {"id", "text"} for r in rows):
        raise ValueError("Predições JSONL exigem somente id e text")
    data = {r["id"]: r["text"] for r in rows}
    if len(data) != len(rows):
        raise ValueError("IDs duplicados nas predições")
    return data


def predict(records: list[dict], model_spec: str, revision=REVISION, device="cpu", threads=2) -> tuple[dict, dict, dict]:
    import numpy as np
    import psutil
    path = Path(model_spec)
    identity = model_identity(model_spec, revision)
    is_onnx = path.is_dir()
    if is_onnx:
        import sherpa_onnx
        model_file = path / "model.int8.onnx"
        if not model_file.is_file():
            model_file = path / "model.onnx"
        recognizer = sherpa_onnx.OfflineRecognizer.from_nemo_ctc(model=str(model_file), tokens=str(path / "tokens.txt"),
                                                              num_threads=threads, sample_rate=16000, feature_dim=80,
                                                              decoding_method="greedy_search", provider="cpu", debug=False)
        actual_device = "cpu"
    else:
        import torch
        actual_device = "cuda" if device == "auto" and torch.cuda.is_available() else ("cpu" if device == "auto" else device)
        if actual_device not in ("cuda", "cpu"):
            raise ValueError("device deve ser auto, cpu ou cuda")
        model, _ = load_nemo(model_spec, revision, device=actual_device)
        model.eval()
        model.preprocessor.featurizer.dither = 0.0
        model.preprocessor.featurizer.pad_to = 0
        if actual_device == "cuda":
            torch.cuda.reset_peak_memory_stats()

    def transcribe(record):
        if is_onnx:
            with wave.open(record["audio_filepath"], "rb") as f:
                if (f.getframerate(), f.getnchannels(), f.getsampwidth()) != (16000, 1, 2):
                    raise ValueError("Avaliação sherpa exige PCM16 mono 16 kHz")
                samples = np.frombuffer(f.readframes(f.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
            stream = recognizer.create_stream()
            stream.accept_waveform(16000, samples)
            recognizer.decode_stream(stream)
            return stream.result.text.strip()
        with torch.inference_mode():
            result = model.transcribe([record["audio_filepath"]], batch_size=1, return_hypotheses=False, verbose=False)
            if isinstance(result, tuple):
                result = result[0]
            value = result[0]
            return value if isinstance(value, str) else value.text

    # Exclude a single warm-up; include complete per-utterance preprocessing in latency.
    if records:
        transcribe(records[0])
    process = psutil.Process()
    rss_peak = process.memory_info().rss
    times, predictions = [], {}
    for index, record in enumerate(records, 1):
        logging.info("Inferência CTC %s/%s: %s", index, len(records), record["id"])
        if not is_onnx and actual_device == "cuda":
            torch.cuda.synchronize()
        start = time.perf_counter()
        predictions[record["id"]] = transcribe(record)
        if not is_onnx and actual_device == "cuda":
            torch.cuda.synchronize()
        times.append(time.perf_counter() - start)
        rss_peak = max(rss_peak, process.memory_info().rss)
    gpu_memory = torch.cuda.max_memory_allocated() if not is_onnx and actual_device == "cuda" else None
    performance = {"device": actual_device, "threads": threads if is_onnx else None,
                   "latency_mean_seconds": statistics.mean(times), "latency_p95_seconds": sorted(times)[max(0, int(len(times) * .95 + .999999) - 1)],
                   "inference_seconds": sum(times), "real_time_factor": sum(times) / sum(r["duration"] for r in records),
                   "sampled_process_rss_peak_bytes": rss_peak, "cuda_peak_allocated_bytes": gpu_memory,
                   "memory_method": "RSS sampled after warm-up and after each utterance; whole process, not model allocation; CUDA allocator peak where applicable",
                   "latency_method": "batch_size=1, one excluded warm-up, preprocessing included; no lexical correction or Wyoming overhead"}
    if is_onnx:
        del recognizer
    else:
        del model
    gc.collect()
    if not is_onnx and actual_device == "cuda":
        torch.cuda.empty_cache()
    return predictions, performance, identity


def evaluate(dataset: Path, output: Path, *, model: str | None = None, baseline_model: str | None = None,
             predictions: Path | None = None, baseline_predictions: Path | None = None,
             split="test", revision=REVISION, device="cpu", threads=2, ffmpeg="ffmpeg", ffprobe="ffprobe") -> dict:
    if bool(model) == bool(predictions) or bool(baseline_model) == bool(baseline_predictions):
        raise ValueError("Forneça um model OU predictions e um baseline-model OU baseline-predictions.")
    if split not in ("test", "validation"):
        raise ValueError("Avaliação permite test ou validation")
    if threads < 1:
        raise ValueError("threads deve ser positivo")
    if not validate(dataset, ffmpeg=ffmpeg, ffprobe=ffprobe)["ready"]:
        raise ValueError("Dataset reprovado")
    manifest = dataset / "manifests" / f"{split}.jsonl"
    records = read_jsonl(manifest)
    if not records:
        raise ValueError("Conjunto de avaliação vazio")
    output = output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Diretório de avaliação já contém dados; use outro --output.")
    outputs = {}
    for name, spec, supplied in (("baseline", baseline_model, baseline_predictions), ("candidate", model, predictions)):
        if supplied:
            inferred = read_predictions(supplied)
            performance, identity = None, {"type": "supplied_predictions", "sha256": digest(supplied)}
        else:
            inferred, performance, identity = predict(records, spec, revision, device, threads)
        scores = score_predictions(records, inferred)
        write_jsonl(output / f"{name}_predictions.jsonl", [{"id": r["id"], "text": inferred[r["id"]]} for r in records])
        write_json(output / f"{name}_details.json", scores)
        outputs[name] = {"identity": identity, "metrics": scores["overall"], "verbatim_metrics": scores["verbatim"],
                         "per_speaker": scores["per_speaker"], "performance": performance}
    baseline, candidate = outputs["baseline"]["metrics"], outputs["candidate"]["metrics"]
    comparison = {"wer_delta": candidate["wer"] - baseline["wer"], "cer_delta": candidate["cer"] - baseline["cer"],
                  "improved": candidate["wer"] < baseline["wer"] and candidate["cer"] <= baseline["cer"]}
    all_speakers = {r["speaker"] for r in read_jsonl(dataset / "metadata.jsonl")}
    tested_speakers = {r["speaker"] for r in records}
    result = {"dataset": str(dataset.resolve()), "split": split, "manifest_sha256": digest(manifest),
              "audio_fingerprints": {r["id"]: digest(Path(r["audio_filepath"])) for r in records},
              "normalization": "NFC, casefold, remove Unicode punctuation, collapse whitespace; accents retained; CER includes spaces",
              "inference_verified": predictions is None and baseline_predictions is None,
              "speaker_coverage": {"evaluated": sorted(tested_speakers), "without_examples_in_this_split": sorted(all_speakers - tested_speakers)},
              "versions": package_versions(), **outputs, "comparison": comparison}
    write_json(output / "comparison.json", result)
    return result
