"""Utterance shadow runner: extraction corpus through mx_shadow vs v1 plans.

Runs the release mx_shadow probe over the frozen mention-extraction corpus
(synthetic fixtures only; never residential data), compares resolved target
sets with v1 plan target sets per ADR-0053 section 5, and writes an
aggregates-only divergence report under target/. No registry ID, entity ID,
mention, span, or utterance-derived value is written to disk. Actions,
ordering, and segmentation are recorded as not_evaluated: the resolver
carries no actions and set comparison cannot prove plan equivalence.
Strictly observational: nothing here feeds execution or modifies v1.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BASE = REPO / "evaluation" / "ptbr-independent" / "mention-extraction"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO / "target" / "ptbr-independent" / "er-session" / "utterance-shadow.json",
    )
    arguments = parser.parse_args()

    gold = {
        json.loads(line)["id"]: json.loads(line)
        for line in (BASE / "corpus-v1.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    }
    completed = subprocess.run(
        [
            str(arguments.probe),
            "--snapshot", str(BASE / "snapshot-v1.json"),
            "--mlp-catalog", str(BASE / "mlp-catalog-v1.json"),
            "--corpus", str(BASE / "corpus-v1.jsonl"),
        ],
        capture_output=True, text=True, check=False,
    )
    if completed.returncode != 0:
        raise SystemExit(f"shadow probe crashed: {completed.stderr[-2000:]}")
    rows = json.loads(completed.stdout)

    classes: dict[str, int] = {}
    outcomes = {"resolved": 0, "ambiguous": 0, "no_match": 0}
    v1_classes = {"plan": 0, "ambiguous": 0, "no_match": 0, "invalid_request": 0}
    extracted = 0

    def bump(name: str) -> None:
        classes[name] = classes.get(name, 0) + 1

    for row in rows:
        want = gold[row["id"]]
        want_extracted = want["expected"]["outcome"] == "extracted"
        want_mentions = sum(len(s["mentions"]) for s in want["expected"].get("segments", []))
        got_mentions = sum(
            1 for r in row["resolutions"] for _ in [r]
        )
        if row["extracted"]:
            extracted += 1
        if row["extracted"] != want_extracted or (row["extracted"] and got_mentions != want_mentions):
            bump("extraction-mismatch")
            continue
        if not row["extracted"]:
            bump("both-abstain")
            continue
        for resolution in row["resolutions"]:
            outcomes[resolution["outcome"]] += 1
        v1_classes[row["v1"]["status"]] += 1
        resolved_ids = {r["registry_id"] for r in row["resolutions"] if r["outcome"] == "resolved"}
        v1_targets = set(row["v1"]["targets"])
        if resolved_ids == v1_targets:
            bump("target-sets-equal")
        elif any(r["outcome"] == "resolved" for r in row["resolutions"]) and row["v1"]["status"] in (
            "ambiguous", "no_match", "invalid_request",
        ):
            bump("resolved-on-v1-reject")
        elif row["v1"]["status"] == "plan":
            bump("target-set-divergence")
        else:
            bump("both-abstain")

    total = len(rows)
    report = {
        "schema_version": 1,
        "cases": total,
        "extracted": extracted,
        "resolution_outcomes": outcomes,
        "v1_outcomes": v1_classes,
        "divergences": classes,
        "false_resolutions": classes.get("resolved-on-v1-reject", 0),
        "structural_errors": classes.get("extraction-mismatch", 0),
        "not_evaluated": ["actions", "ordering", "segmentation", "plan-equivalence"],
        "note": "target-set comparison is partial diagnostics only; equal sets never declare equivalent plans",
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, sort_keys=True))
    print(f"wrote {arguments.output}")


if __name__ == "__main__":
    main()
