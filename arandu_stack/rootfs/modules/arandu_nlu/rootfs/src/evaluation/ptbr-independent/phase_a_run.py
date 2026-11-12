"""Run frozen Phase A candidates against local HTTP v1 for diagnosis."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from arandu_adapter import AdapterError, LocalServer, health, interpret, local_endpoint, start_local
from phase_a import cases, compare
from phase_a_manifest import MANIFEST, verify_manifest
from semantic import load_json

ROOT = Path(__file__).resolve().parent


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--binary", type=Path)
    source.add_argument("--endpoint")
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def run_cases(endpoint: str, product_catalog: Any, semantic_catalog: Any) -> list[dict[str, str]]:
    health(endpoint)
    results: list[dict[str, str]] = []
    for item in cases():
        try:
            actual = load_json(json.dumps(interpret(endpoint, item["text"], product_catalog), ensure_ascii=False))
            result = compare(item, actual, semantic_catalog)
            results.append({"id": item["id"], "classification": result.classification, "detail": result.detail})
        except AdapterError as error:
            results.append({"id": item["id"], "classification": "protocol_error", "detail": str(error)})
    return results


def main() -> None:
    arguments = parse_arguments()
    verify_manifest(json.loads(MANIFEST.read_text(encoding="utf-8")))
    semantic_catalog = __import__("corpus").load_fixtures(ROOT)[0]
    product_catalog = load_json((ROOT / "data" / "catalog-v1.json").read_text(encoding="utf-8"))
    server: LocalServer | None = None
    try:
        if arguments.binary:
            server = start_local(arguments.binary.resolve())
            endpoint = server.endpoint
        else:
            endpoint = arguments.endpoint
            local_endpoint(endpoint)
        results = run_cases(endpoint, product_catalog, semantic_catalog)
    finally:
        if server:
            server.close()
    report = {"schema_version": 1, "freeze_id": "ptbr-independent-phase-a", "endpoint": endpoint, "cases": results}
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    failures = [item for item in results if item["classification"] != "exact_pass"]
    print(f"Phase A diagnostic wrote {len(results)} cases to {arguments.output}")
    if failures:
        raise SystemExit(f"Phase A diagnostic has {len(failures)} non-exact results")


if __name__ == "__main__":
    main()
