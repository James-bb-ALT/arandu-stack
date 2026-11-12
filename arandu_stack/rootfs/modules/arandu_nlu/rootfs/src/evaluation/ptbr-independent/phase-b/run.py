"""Run independent Phase B corpus through local HTTP v1."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "evaluation" / "ptbr-independent"))
from arandu_adapter import AdapterError, LocalServer, interpret, start_local  # noqa: E402
from semantic import compare_case, validate_catalog  # noqa: E402

from common import CATALOG_PATH, CORPUS_PATH  # noqa: E402


def load_catalog() -> dict[str, Any]:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def load_cases() -> list[dict[str, Any]]:
    return [json.loads(line) for line in CORPUS_PATH.read_text(encoding="utf-8").splitlines() if line]


def main() -> None:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--binary", type=Path)
    source.add_argument("--endpoint")
    parser.add_argument("--output", type=Path, default=ROOT / "target" / "ptbr-independent-phase-b" / "run-v1.json")
    args = parser.parse_args()
    catalog = load_catalog()
    semantic_catalog = validate_catalog(catalog, require_primary=True)
    cases = load_cases()
    server: LocalServer | None = None
    results: list[dict[str, Any]] = []
    try:
        if args.binary is not None:
            server = start_local(args.binary.resolve())
            endpoint = server.endpoint
        else:
            endpoint = args.endpoint
        for row in cases:
            try:
                actual = interpret(endpoint, row["text"], catalog)
                comparison = compare_case(row["expected"], actual, semantic_catalog)
                result = {"id": row["id"], "actual": actual, "classification": comparison.classification, "detail": comparison.detail}
            except AdapterError as error:
                result = {"id": row["id"], "classification": "protocol_error", "detail": str(error)}
            results.append(result)
    finally:
        if server is not None:
            results.append({"server_memory": server.memory_bytes()})
            server.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {"schema_version": 1, "freeze_id": "ptbr-independent-phase-b-v1", "cases": results}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    failures = [row for row in results if row.get("classification") != "exact_pass"]
    print(json.dumps({"cases": len(cases), "failures": len(failures), "output": str(args.output)}, sort_keys=True))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
