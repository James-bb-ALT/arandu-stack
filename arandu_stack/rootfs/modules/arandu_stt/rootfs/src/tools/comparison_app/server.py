"""Local web comparison. All audio stays on this computer."""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys
import tempfile
import threading
import time
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import numpy as np
import uvicorn

from stt_pipeline.audio import AudioTools, wav_details
from stt_pipeline.common import canonical, read_json, read_jsonl
from stt_pipeline.evaluation import error_counts


def create_app(repo: Path, experiment: Path, baseline_model: Path | None = None) -> FastAPI:
    configured_baseline = baseline_model is not None
    baseline_model = baseline_model or repo / "models" / "original_int8"
    app = FastAPI(title="Arandu · Comparação acústica", docs_url=None, redoc_url=None)
    metadata = read_jsonl(repo / "dataset" / "metadata.jsonl")
    by_id = {r["id"]: r for r in metadata}
    split_by_id = {r["id"]: split for split in ("train", "validation", "test")
                   for r in read_jsonl(repo / "dataset" / "manifests" / f"{split}.jsonl")}
    engines = {}
    lock = threading.Lock()

    def read_optional(path, default=None):
        try:
            return read_json(path) if path.exists() else default
        except (ValueError, OSError):
            return default

    def candidate_directory():
        # Never hand an in-progress ONNX write to the C++ runtime.
        if not (experiment / "experiment.json").is_file():
            return None
        result = read_optional(experiment / "experiment.json", {})
        if result.get("export", {}).get("ready_for_manual_deployment"):
            candidates = (experiment / "export" / "int8",)
        else:
            candidates = (experiment / "export" / "fp32", experiment / "review_model")
        for directory in candidates:
            if (directory / "tokens.txt").is_file() and any((directory / name).is_file() for name in ("model.int8.onnx", "model.onnx")):
                return directory
        return None

    def infer(directory, audio):
        import sherpa_onnx
        model = directory / "model.int8.onnx"
        if not model.exists():
            model = directory / "model.onnx"
        signature = (str(model), model.stat().st_mtime_ns)
        if signature not in engines:
            for stale in list(engines):
                if stale[0] == str(model):
                    del engines[stale]
            if len(engines) >= 2:
                del engines[next(iter(engines))]
            engines[signature] = sherpa_onnx.OfflineRecognizer.from_nemo_ctc(
                model=str(model), tokens=str(directory / "tokens.txt"), num_threads=2,
                sample_rate=16000, feature_dim=80, decoding_method="greedy_search", provider="cpu", debug=False)
        before = time.perf_counter()
        stream = engines[signature].create_stream()
        stream.accept_waveform(16000, audio)
        engines[signature].decode_stream(stream)
        return {"text": stream.result.text.strip(), "latency_ms": (time.perf_counter() - before) * 1000,
                "format": "INT8" if "int8" in model.name else "FP32"}

    def comparison(path, reference=None):
        if reference is not None and not canonical(reference):
            reference = None
        details = wav_details(path, 16000)
        if details["duration"] > 35:
            raise HTTPException(422, "Limite de áudio: 35 segundos.")
        with wave.open(str(path), "rb") as f:
            audio = np.frombuffer(f.readframes(f.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
        with lock:
            original = infer(baseline_model, audio)
            directory = candidate_directory()
            adapted = infer(directory, audio) if directory else None
        for result in (original, adapted):
            if result and reference:
                counts = error_counts(reference, result["text"])
                result["metrics"] = {**counts, "wer": counts["word_errors"] / counts["reference_words"],
                                     "cer": counts["character_errors"] / counts["reference_characters"]}
        return {"original": original, "adapted": adapted, "reference": reference, "duration": details["duration"]}

    @app.get("/api/status")
    def status():
        baseline = read_optional(repo / "evaluations" / "original_seed57" / "comparison.json")
        report = (read_optional(experiment / "native_evaluation" / "comparison.json")
                  or read_optional(experiment / "comparison_runtime.json") or read_optional(experiment / "comparison.json"))
        return {"experiment_path": str(experiment), "baseline_model_path": str(baseline_model),
                "model_labels": {"baseline": "ANTERIOR 2.1" if configured_baseline else "ORIGINAL",
                                 "candidate": "NOVO TREINO" if configured_baseline else "ADAPTADO",
                                 "baseline_format": "INT8" if (baseline_model / "model.int8.onnx").exists() else "FP32"},
                "progress": read_optional(experiment / "progress.json", {"phase": "environment", "message": "Preparando ambiente de treinamento"}),
                "training": read_optional(experiment / "run.json"), "comparison": report,
                "baseline": baseline["baseline"] if baseline else None,
                "history": read_optional(experiment / "history.json", []),
                "dataset": read_json(repo / "dataset" / "reports" / "statistics.json"),
                "candidate_available": candidate_directory() is not None,
                "export": read_optional(experiment / "experiment.json")}

    @app.get("/api/samples")
    def samples():
        return [{"id": r["id"], "speaker": r["speaker"], "text": r["text"], "duration": r["duration"],
                 "split": split_by_id[r["id"]]} for r in metadata]

    @app.get("/api/audio/{sample_id}")
    def audio(sample_id: str):
        if sample_id not in by_id:
            raise HTTPException(404, "Gravação não encontrada.")
        return FileResponse(by_id[sample_id]["audio_filepath"], media_type="audio/wav")

    @app.get("/api/compare/{sample_id}")
    def compare(sample_id: str):
        if sample_id not in by_id:
            raise HTTPException(404, "Gravação não encontrada.")
        row = by_id[sample_id]
        return comparison(Path(row["audio_filepath"]), row["text"])

    @app.post("/api/compare-upload")
    async def upload(request: Request):
        # Raw-body upload works with files and browser MediaRecorder, without
        # storing personal audio or accepting caller-selected filesystem paths.
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 15 * 1024 * 1024:
                raise HTTPException(413, "Arquivo excede 15 MB.")
        if not data:
            raise HTTPException(422, "Áudio vazio.")
        with tempfile.TemporaryDirectory(prefix="arandu-compare-") as temporary:
            source, target = Path(temporary) / "input.media", Path(temporary) / "decoded.wav"
            source.write_bytes(data)
            tools = AudioTools()
            try:
                info = tools.probe(source)
                if float(info.get("source_duration") or 0) > 35:
                    raise ValueError("Limite de áudio: 35 segundos.")
                tools.convert(source, target, info, 16000, "upload", {})
                reference = request.query_params.get("reference", "").strip() or None
                if reference and len(reference) > 2000:
                    raise ValueError("Referência excede 2.000 caracteres.")
                return comparison(target, reference)
            except (ValueError, OSError) as exc:
                raise HTTPException(422, str(exc)) from exc

    @app.get("/api/results")
    def results():
        directory = next((experiment / name for name in ("native_evaluation", "runtime_evaluation", "evaluation")
                          if (experiment / name / "comparison.json").exists()), experiment / "evaluation")
        baseline = read_optional(directory / "baseline_details.json")
        candidate = read_optional(directory / "candidate_details.json")
        return {"baseline": baseline, "candidate": candidate}

    app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="ui")
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--baseline-model", type=Path)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    uvicorn.run(create_app(args.repo.resolve(), args.experiment.resolve(),
                           args.baseline_model.resolve() if args.baseline_model else None), host="127.0.0.1", port=args.port)
