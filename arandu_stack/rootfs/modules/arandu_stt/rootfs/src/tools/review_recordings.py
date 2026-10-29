#!/usr/bin/env python3
"""Acoustic evidence for manual audio/script mapping; never creates manifests."""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from stt_pipeline.audio import AudioTools
from stt_pipeline.common import require_disjoint, slug, write_csv, write_json, write_jsonl
from stt_pipeline.dataset import discover, load_mapping
from stt_pipeline.evaluation import error_counts, predict


def review(input_dir: Path, output: Path, model_dir: Path, mapping_path: Path | None = None,
           ffmpeg="ffmpeg", ffprobe="ffprobe", threads=2):
    input_dir, output = input_dir.resolve(), output.resolve()
    require_disjoint(input_dir, output)
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use um diretório de revisão novo/vazio")
    tools = AudioTools(ffmpeg, ffprobe)
    mapping = load_mapping(mapping_path)
    files, scripts, issues = discover(input_dir, mapping, tools)
    records, rejected = [], []
    for row in files:
        if row["status"] != "discovered":
            rejected.append(row)
            continue
        uid = slug(row["source"])
        target = output / "audio" / (uid + ".wav")
        try:
            logging.info("Decodificando para revisão: %s", row["source"])
            converted = tools.convert(Path(row["source_path"]), target, row, 16000, row["source_sha256"], {})
            records.append({**row, **converted, "id": uid, "audio_filepath": str(target)})
        except (ValueError, OSError) as exc:
            rejected.append({**row, "message": str(exc)})
    if not records:
        raise ValueError("Nenhum áudio decodificável")
    predictions, performance, identity = predict(records, str(model_dir), threads=threads)
    rows = []
    for record in records:
        candidates = []
        for key, script in scripts.items():
            if Path(key).parts[0] != record["speaker"]:
                continue
            for pid, phrase in script.phrases.items():
                counts = error_counts(phrase["text"], predictions[record["id"]])
                wer = counts["word_errors"] / counts["reference_words"] if counts["reference_words"] else float("inf")
                candidates.append((wer, key, pid, phrase["text"]))
        candidates.sort()
        best = candidates[0] if candidates else (None, "", "", "")
        runner_up = candidates[1][0] if len(candidates) > 1 else None
        rows.append({"speaker": record["speaker"], "source": record["source"], "duration": record["duration"],
                     "hypothesis": predictions[record["id"]], "proposed_script": best[1], "proposed_phrase_id": best[2],
                     "script_text": best[3], "word_error_rate_against_script": best[0], "runner_up_wer": runner_up,
                     "word_errors": error_counts(best[3], predictions[record["id"]])["word_errors_detail"],
                     "association_confirmed": False})
    write_json(output / "review.json", {"model": identity, "performance": performance, "rows": rows,
                                       "rejected": rejected, "script_issues": issues,
                                       "scope": "ASR hypotheses and proposed matches are review evidence, never training transcripts or automatic approval"})
    write_jsonl(output / "audio_metadata.jsonl", records)
    write_csv(output / "review.csv", rows, ["speaker", "source", "duration", "hypothesis", "proposed_script", "proposed_phrase_id",
                                          "script_text", "word_error_rate_against_script", "runner_up_wer", "association_confirmed"])
    return {"decoded": len(records), "rejected": len(rejected), "review_rows": len(rows)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--mapping", type=Path)
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args()
    print(review(args.input, args.output, args.model_dir, args.mapping, threads=args.threads))
