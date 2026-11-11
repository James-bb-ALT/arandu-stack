"""Reusable Phase A/B regression gate for post-freeze engine builds.

Compares live HTTP outputs of the given binary against the historical baselines
(`baselines/v2/run-v2.json`, `baselines/phase-b-v1/run-v1.json`) without editing
any freeze and without weakening any official validator:

- frozen inputs are verified with the official freeze code (v2 catalog/dataset/
  authorship sections, phase-b freeze script, Phase A manifest script);
- `run_v2.py --binary` still refuses while `engine_source` differs (fail-closed
  by design); this gate replays through the same official loaders, semantic
  oracle and HTTP adapter instead of duplicating their semantics;
- the expected `engine_source` divergence is a committed record
  (`entity-resolution/engine-source-divergence.json`); the gate asserts the
  current tree matches that record exactly, so any further src drift fails.

Exit 0 only when every baseline comparison matches.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
REPOSITORY = ROOT.parent.parent
sys.path.insert(0, str(ROOT))

from arandu_adapter import interpret as http_interpret  # noqa: E402
from arandu_adapter import local_endpoint, start_local  # noqa: E402
from corpus import load_fixtures  # noqa: E402
from freeze_v2 import MANIFEST, REPOSITORY as FREEZE_REPO  # noqa: E402
from freeze_v2 import build_manifest, tree_digest  # noqa: E402
from semantic import compare_case, load_json, validate_catalog  # noqa: E402

sys.path.insert(0, str(ROOT / "phase-b"))
from common import CATALOG_PATH as PHASE_B_CATALOG  # noqa: E402
from common import CORPUS_PATH as PHASE_B_CORPUS  # noqa: E402

BASELINE_A = REPOSITORY / "evaluation" / "ptbr-independent" / "baselines" / "v2" / "run-v2.json"
BASELINE_B = (
    REPOSITORY / "evaluation" / "ptbr-independent" / "baselines" / "phase-b-v1" / "run-v1.json"
)
DIVERGENCE = ROOT / "entity-resolution" / "engine-source-divergence.json"
DEFAULT_OUTPUT = REPOSITORY / "target" / "ptbr-independent" / "regression-gate.json"


def check_official_scripts() -> None:
    scripts = [
        [sys.executable, str(ROOT / "phase-b" / "freeze.py"), "--check"],
        [sys.executable, str(ROOT / "phase_a_manifest.py"), "--check"],
        [sys.executable, str(ROOT / "entity-resolution" / "freeze.py"), "--check"],
        [sys.executable, str(ROOT / "entity-resolution" / "generate.py"), "--check"],
        [sys.executable, str(ROOT / "entity-resolution" / "oracle.py")],
    ]
    for command in scripts:
        subprocess.run(command, check=True, capture_output=True, text=True)


def check_frozen_inputs_and_engine() -> dict[str, Any]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    fresh = build_manifest()
    for section in ("catalog", "dataset", "data_authorship"):
        if manifest.get(section) != fresh[section]:
            raise SystemExit(f"regression gate: frozen input drift in {section}")
    record = json.loads(DIVERGENCE.read_text(encoding="utf-8"))
    if manifest["engine_source"]["sha256"] != record["phase_a_freeze"]["engine_source_digest"]:
        raise SystemExit("regression gate: divergence record does not match the frozen manifest")
    current_digest, _ = tree_digest(FREEZE_REPO / "addon" / "engine" / "src")
    if current_digest != record["current_engine_source_digest"]:
        raise SystemExit(
            "regression gate: engine source changed beyond the recorded divergence; "
            "investigate and version the divergence record"
        )
    return {
        "engine_source_divergence": "recorded",
        "manifest_engine_digest": manifest["engine_source"]["sha256"],
        "current_engine_digest": current_digest,
    }


def replay_phase_a(endpoint: str) -> list[dict[str, Any]]:
    catalog, cases = load_fixtures(ROOT, version=2)
    raw_catalog = load_json((ROOT / "data" / "catalog-v2.json").read_text(encoding="utf-8"))
    from run_v2 import run_cases

    return run_cases(endpoint, raw_catalog, cases, catalog)


def replay_phase_b(endpoint: str) -> list[dict[str, Any]]:
    catalog = json.loads(PHASE_B_CATALOG.read_text(encoding="utf-8"))
    semantic_catalog = validate_catalog(catalog, require_primary=True)
    cases = [
        json.loads(line)
        for line in PHASE_B_CORPUS.read_text(encoding="utf-8").splitlines()
        if line
    ]
    results = []
    for row in cases:
        actual = http_interpret(endpoint, row["text"], catalog)
        comparison = compare_case(row["expected"], actual, semantic_catalog)
        results.append(
            {"id": row["id"], "actual": actual, "classification": comparison.classification}
        )
    return results


def compare_against_baselines(
    results_a: list[dict[str, Any]], results_b: list[dict[str, Any]]
) -> dict[str, Any]:
    baseline_a = {
        case["id"]: case["classification"]
        for case in json.loads(BASELINE_A.read_text(encoding="utf-8"))["cases"]
    }
    baseline_b = {
        case["id"]: (case.get("classification"), case.get("actual"))
        for case in json.loads(BASELINE_B.read_text(encoding="utf-8"))["cases"]
        if "id" in case
    }
    mismatches = []
    for result in results_a:
        if baseline_a.get(result["id"]) != result["classification"]:
            mismatches.append({"suite": "phase-a", **result})
    for result in results_b:
        expected = baseline_b.get(result["id"])
        if expected != (result["classification"], result["actual"]):
            mismatches.append({"suite": "phase-b", **result})
    missing_a = len(results_a) != len(baseline_a)
    missing_b = len(results_b) != len(baseline_b)
    return {
        "phase_a": {"cases": len(results_a), "baseline_cases": len(baseline_a)},
        "phase_b": {"cases": len(results_b), "baseline_cases": len(baseline_b)},
        "count_mismatch": bool(missing_a or missing_b),
        "mismatches": mismatches,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--binary", type=Path)
    source.add_argument("--endpoint")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()

    check_official_scripts()
    engine = check_frozen_inputs_and_engine()
    server = None
    try:
        if arguments.binary is not None:
            server = start_local(arguments.binary.resolve())
            endpoint = server.endpoint
        else:
            endpoint = arguments.endpoint
            local_endpoint(endpoint)
        results_a = replay_phase_a(endpoint)
        results_b = replay_phase_b(endpoint)
    finally:
        if server is not None:
            server.close()
    comparison = compare_against_baselines(results_a, results_b)
    report = {
        "schema_version": 1,
        "gate": "ptbr-independent-phase-ab-regression",
        "engine": engine,
        "comparison": comparison,
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "phase_a": f"{len(results_a) - sum(1 for m in comparison['mismatches'] if m['suite'] == 'phase-a')}/{len(results_a)} match",
                "phase_b": f"{len(results_b) - sum(1 for m in comparison['mismatches'] if m['suite'] == 'phase-b')}/{len(results_b)} match",
                "output": str(arguments.output),
            },
            sort_keys=True,
        )
    )
    if comparison["mismatches"] or comparison["count_mismatch"]:
        raise SystemExit(
            f"regression gate FAILED: {len(comparison['mismatches'])} mismatches"
        )
    print("REGRESSION GATE PASS")


if __name__ == "__main__":
    main()
