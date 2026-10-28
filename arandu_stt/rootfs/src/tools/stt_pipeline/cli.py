from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys

from .dataset import inspect_dataset, prepare, validate
from .evaluation import evaluate
from .exporting import export_model
from .models import REVISION
from .training import train


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Arandu STT: preparação offline, NeMo, avaliação CTC e exportação revisável.")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("inspect", "prepare", "validate", "train", "evaluate", "export"):
        command = commands.add_parser(name)
        command.add_argument("--ffmpeg", default="ffmpeg")
        command.add_argument("--ffprobe", default="ffprobe")
        command.add_argument("--verbose", action="store_true")
        command.add_argument("--log-file", type=Path)
        if name in ("inspect", "prepare"):
            command.add_argument("--input", type=Path, required=True)
            command.add_argument("--mapping", type=Path)
        else:
            command.add_argument("--dataset", type=Path, required=True)
        if name != "validate":
            command.add_argument("--output", type=Path, required=True)
        if name == "prepare":
            command.add_argument("--sample-rate", type=int, default=16000)
            command.add_argument("--min-duration", type=float, default=.15)
            command.add_argument("--max-duration", type=float, default=35.0)
            command.add_argument("--ratios", type=float, nargs=3, default=(.8, .1, .1), metavar=("TRAIN", "VALIDATION", "TEST"))
            command.add_argument("--seed", type=int, default=42)
            command.add_argument("--split-mode", choices=("phrase", "speaker"), default="phrase")
        if name == "train":
            command.add_argument("--config", type=Path)
            command.add_argument("--resume", type=Path)
        if name == "evaluate":
            candidate = command.add_mutually_exclusive_group(required=True)
            candidate.add_argument("--model")
            candidate.add_argument("--predictions", type=Path)
            baseline = command.add_mutually_exclusive_group(required=True)
            baseline.add_argument("--baseline-model")
            baseline.add_argument("--baseline-predictions", type=Path)
            command.add_argument("--split", choices=("validation", "test"), default="test")
            command.add_argument("--revision", default=REVISION)
            command.add_argument("--device", choices=("auto", "cpu", "cuda"), default="cpu")
            command.add_argument("--threads", type=int, default=2)
        if name == "export":
            command.add_argument("--checkpoint", type=Path, required=True)
            command.add_argument("--comparison", type=Path, required=True)
            command.add_argument("--max-wer-regression", type=float, default=.005)
            command.add_argument("--max-cer-regression", type=float, default=.005)
            command.add_argument("--threads", type=int, default=2)
    return root


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    arguments = parser().parse_args(argv)
    options = vars(arguments).copy()
    command = options.pop("command")
    verbose = options.pop("verbose")
    log_file = options.pop("log_file")
    handlers = [logging.StreamHandler()]
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", handlers=handlers, force=True)
    try:
        if command in ("inspect", "prepare"):
            options["input_dir"] = options.pop("input")
            options["output_dir"] = options.pop("output")
            options["mapping_path"] = options.pop("mapping")
        elif command == "validate":
            options["output_dir"] = options.pop("dataset")
        elif command == "train":
            options["config_path"] = options.pop("config")
        operation = {"inspect": inspect_dataset, "prepare": prepare, "validate": validate,
                     "train": train, "evaluate": evaluate, "export": export_model}[command]
        result = operation(**options)
        if command == "inspect":
            summary = {"files": len(result["files"]), "scripts": len(result["scripts"]), "issues": len(result["issues"]),
                       "rejected_files": sum(r["status"] == "rejected" for r in result["files"])}
        elif command == "evaluate":
            summary = {"baseline": result["baseline"]["metrics"], "candidate": result["candidate"]["metrics"], "comparison": result["comparison"]}
        else:
            summary = result
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if command == "prepare":
            return 2 if result["split_error"] or result["rejected_examples"] or result["issues"] else 0
        if command == "validate":
            return 0 if result["ready"] else 2
        if command == "export":
            return 0 if result["ready_for_manual_deployment"] else 2
        return 0
    except ImportError as exc:
        logging.error("Dependência indisponível: %s. Preparação usa apenas stdlib/FFmpeg; consulte requirements de treino/exportação.", exc)
        return 1
    except (ValueError, OSError, LookupError, TypeError, RuntimeError) as exc:
        logging.error("%s", exc, exc_info=verbose)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
