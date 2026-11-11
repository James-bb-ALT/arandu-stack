"""Run frozen independent PT-BR evaluation through public local HTTP v1."""
from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path
from typing import Any

from arandu_adapter import AdapterError, LocalServer, health, interpret, local_endpoint, start_local
from corpus import load_fixtures
from freeze import MANIFEST, sha256_file, verify_manifest
from reporting import summary, write_report
from semantic import Comparison, compare_case, load_json

ROOT = Path(__file__).resolve().parent
REPOSITORY = ROOT.parent.parent
DEFAULT_OUTPUT = REPOSITORY / "target" / "ptbr-independent"


def source_hashes(binary: Path | None) -> dict[str, str]:
    paths = {
        "adapter": ROOT / "arandu_adapter.py",
        "comparison": ROOT / "semantic.py",
        "runner": ROOT / "run.py",
        "catalog": ROOT / "data" / "catalog-v1.json",
        "dataset": ROOT / "data" / "dataset-v1.jsonl",
    }
    if binary is not None:
        paths["binary"] = binary
    return {name: sha256_file(path) for name, path in paths.items()}


def case_result(case: dict[str, Any], comparison: Comparison) -> dict[str, Any]:
    return {
        "id": case["id"],
        "bucket": case["bucket"],
        "dimensions": case["raw"]["dimensions"],
        "classification": comparison.classification,
        "detail": comparison.detail,
    }


def run_cases(endpoint: str, product_catalog: Any, cases: list[dict[str, Any]], semantic_catalog: Any) -> list[dict[str, Any]]:
    health(endpoint)
    results: list[dict[str, Any]] = []
    for case in cases:
        try:
            actual = load_json(json.dumps(interpret(endpoint, case["raw"]["text"], product_catalog)))
            comparison = compare_case(case["expected"], actual, semantic_catalog)
        except AdapterError as error:
            comparison = Comparison("protocol_error", str(error))
        results.append(case_result(case, comparison))
    return results


def build_report(endpoint: str, cases: list[dict[str, Any]], results: list[dict[str, Any]], binary: Path | None) -> dict[str, Any]:
    by_dimension: dict[str, dict[str, dict[str, int]]] = {}
    for result in results:
        for dimension, value in result["dimensions"].items():
            counts = by_dimension.setdefault(dimension, {}).setdefault(value, {})
            counts[result["classification"]] = counts.get(result["classification"], 0) + 1
    return {
        "schema_version": 1,
        "freeze_id": "ptbr-independent-v1",
        "endpoint": endpoint,
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "hashes": source_hashes(binary),
        "summary": summary(results),
        "dimensions": by_dimension,
        "cases": results,
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--binary", type=Path, help="local release binary to start")
    source.add_argument("--endpoint", help="existing loopback HTTP origin")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    if not MANIFEST.exists():
        raise SystemExit(f"freeze manifest does not exist: {MANIFEST}")
    verify_manifest(json.loads(MANIFEST.read_text(encoding="utf-8")))
    catalog, cases = load_fixtures(ROOT)
    server: LocalServer | None = None
    binary = arguments.binary.resolve() if arguments.binary else None
    output_dir = arguments.output_dir.resolve()
    results: list[dict[str, Any]] = []
    try:
        if binary is not None:
            server = start_local(binary)
            endpoint = server.endpoint
        else:
            endpoint = arguments.endpoint
            local_endpoint(endpoint)
        raw_catalog = load_json((ROOT / "data" / "catalog-v1.json").read_text(encoding="utf-8"))
        results = run_cases(endpoint, raw_catalog, cases, catalog)
        report = build_report(endpoint, cases, results, binary)
        write_report(report, output_dir / "run-v1.json", output_dir / "run-v1.md")
    finally:
        if server is not None:
            server.close()
    scored_failures = [
        result
        for result in results
        if result["bucket"] != "known-gap-future" and result["classification"] != "exact_pass"
    ]
    print(f"ptbr-independent run wrote {len(results)} cases to {output_dir}")
    if scored_failures:
        raise SystemExit(f"ptbr-independent run has {len(scored_failures)} scored divergences")


if __name__ == "__main__":
    main()
