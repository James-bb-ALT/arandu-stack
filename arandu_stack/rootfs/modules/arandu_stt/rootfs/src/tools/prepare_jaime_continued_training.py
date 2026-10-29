"""Snapshot completed robotic clips plus original humans for continued ASR training."""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stt_pipeline.audio import wav_details
from stt_pipeline.common import canonical, digest, read_json, read_jsonl, write_json, write_jsonl
from stt_pipeline.dataset import validate


def focused(text):
    return bool(set(canonical(text).split()) & {"ventilador", "jaime"})


def prepare(repo: Path, output: Path, *, human_repeats=2, focus_repeats=6):
    if not 1 <= human_repeats <= focus_repeats <= 10:
        raise ValueError("Human repetition must satisfy 1 <= human <= focus <= 10.")
    output.mkdir(parents=True, exist_ok=True)
    destination = output / "source/dataset"
    if destination.exists():
        raise ValueError("Snapshot already exists; choose a new output directory.")
    checkpoint = repo / "runs/rtx3080_20261013/best.nemo"
    previous = read_json(repo / "runs/rtx3080_20261013/run.json")
    if digest(checkpoint) != previous["checkpoint_sha256"]:
        raise ValueError("Previous trained checkpoint does not match its recorded hash.")
    generated = repo / "runs/jaime_robotico_500"
    records = [read_json(path) for path in sorted((generated / "records").glob("*.json"))]
    if not records:
        raise ValueError("No completed clips found.")
    shutil.copytree(repo / "dataset", destination)
    humans = read_jsonl(destination / "metadata.jsonl")
    for row in humans:
        relative = Path(row["audio_filepath"]).relative_to(repo / "dataset/audio")
        row["audio_filepath"] = str((destination / "audio" / relative).resolve())
        row["synthetic"] = False
        if row["split"] == "train":
            row["training_repeats"] = focus_repeats if focused(row["text"]) else human_repeats
    # Never train on an exact phrase reserved for the original held-out sets.
    heldout_text = {canonical(row["text"]) for row in humans if row["split"] != "train"}
    synthetics, rejected = [], []
    for record in records:
        source = Path(record["audio_filepath"])
        if digest(source) != record["robotic"]["sha256"]:
            raise ValueError(f"Generated clip changed: {source}")
        details = wav_details(source, 16000)
        if canonical(record["text"]) in heldout_text:
            rejected.append({"id": record["id"], "reason": "human_heldout_phrase_overlap"})
            continue
        identifier = f"jaime_synthetic_robot_{record['id']:04d}"
        target = destination / "audio/jaime_synthetic_robot" / f"{identifier}.wav"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        synthetics.append({"id": identifier, "speaker": "Jaime (clone sintético robótico)",
                           "audio_filepath": str(target.resolve()), "text": record["text"],
                           "duration": details["duration"], "split": "train", "synthetic": True,
                           "status": "valid", "wav_sha256": digest(target),
                           "source_path": str(source), "source_record_id": record["id"],
                           "reference_sha256": record["reference_sha256"],
                           "training_repeats": 3 if focused(record["text"]) else 1,
                           **details})
    rows = humans + synthetics
    write_jsonl(destination / "metadata.jsonl", rows)
    fields = ("id", "speaker", "audio_filepath", "text", "duration", "training_repeats", "synthetic")
    for split in ("train", "validation", "test"):
        write_jsonl(destination / "manifests" / f"{split}.jsonl",
                    [{key: row[key] for key in fields if key in row} for row in rows if row["split"] == split])
    stats = read_json(destination / "reports/statistics.json")
    stats.update(valid_examples=len(rows), participants=len({row["speaker"] for row in rows}),
                 total_duration_seconds=sum(row["duration"] for row in rows),
                 valid_per_speaker=dict(Counter(row["speaker"] for row in rows)))
    stats["splits"] = {split: {"examples": sum(row["split"] == split for row in rows),
                               "duration_seconds": sum(row["duration"] for row in rows if row["split"] == split)}
                       for split in ("train", "validation", "test")}
    write_json(destination / "reports/statistics.json", stats)
    write_json(destination / "reports/release.json", {"ready": False, "reason": "validation_pending"})
    training = [row for row in rows if row["split"] == "train"]
    report = {"parent_checkpoint": str(checkpoint), "parent_checkpoint_sha256": digest(checkpoint),
              "parent_run": "rtx3080_20261013", "human_examples": len(humans),
              "completed_synthetic_clips": len(records), "synthetic_training_examples": len(synthetics),
              "rejected": rejected, "training_unique_examples": len(training),
              "effective_training_examples_per_epoch_before_ctc_filter": sum(row["training_repeats"] for row in training),
              "word_counts": {word: {"synthetic_train": sum(word in canonical(row["text"]).split() for row in synthetics),
                                     "human_train": sum(word in canonical(row["text"]).split() for row in humans if row["split"] == "train"),
                                     "human_validation": sum(word in canonical(row["text"]).split() for row in humans if row["split"] == "validation"),
                                     "human_test": sum(word in canonical(row["text"]).split() for row in humans if row["split"] == "test")}
                              for word in ("ventilador", "jaime")},
              "sampling": {"human": human_repeats, "human_focus_word": focus_repeats, "synthetic": 1, "synthetic_focus_word": 3},
              "heldout_human_content_unchanged": True, "production_replaced": False}
    release = validate(destination)
    if not release["ready"]:
        raise ValueError(f"Dataset validation failed: {release['issues'][:3]}")
    report["dataset_validation"] = release
    write_json(output / "dataset_preparation.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--human-repeats", type=int, default=2)
    parser.add_argument("--focus-repeats", type=int, default=6)
    arguments = parser.parse_args()
    report = prepare(arguments.repo.resolve(), arguments.output.resolve(), human_repeats=arguments.human_repeats,
                     focus_repeats=arguments.focus_repeats)
    print({key: report[key] for key in ["synthetic_training_examples", "training_unique_examples", "word_counts"]})
