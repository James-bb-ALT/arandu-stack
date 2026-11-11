"""Strict loader and static checks for independent evaluation fixtures."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from semantic import ValidationError, load_json, validate_case, validate_catalog

DIMENSIONS = frozenset(
    {
        "action",
        "area",
        "case",
        "chain",
        "domain",
        "gap",
        "normalization",
        "percentage",
        "rejection",
        "targeting",
    }
)


def load_catalog(path: Path, *, require_primary: bool = False) -> tuple[str, Any]:
    raw = load_json(path.read_text(encoding="utf-8"))
    index = validate_catalog(raw, require_primary=require_primary)
    return path.stem, index


def load_cases(path: Path, catalog_id: str, catalog: Any) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_text: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line:
            raise ValidationError(f"dataset line {line_number} is blank")
        case = validate_case(load_json(line), catalog_id, catalog)
        if case["id"] in seen_ids:
            raise ValidationError(f"duplicate case id: {case['id']}")
        raw = load_json(line)
        if raw["text"] in seen_text:
            raise ValidationError(f"duplicate case text at line {line_number}")
        unknown_dimensions = set(raw["dimensions"]) - DIMENSIONS
        if unknown_dimensions:
            raise ValidationError(f"unknown dimensions at line {line_number}: {sorted(unknown_dimensions)}")
        if raw["provenance"]["kind"] != "PROJECT_AUTHORED_SYNTHETIC":
            raise ValidationError(f"case {case['id']} has non-project provenance")
        if raw["provenance"]["license"] != "Apache-2.0":
            raise ValidationError(f"case {case['id']} is not Apache-2.0")
        seen_ids.add(case["id"])
        seen_text.add(raw["text"])
        cases.append({**case, "raw": raw})
    if len(cases) != 144:
        raise ValidationError(f"dataset must contain 144 cases, got {len(cases)}")
    counts = Counter(case["bucket"] for case in cases)
    expected = {"expected-pass-now": 72, "expected-reject-now": 48, "known-gap-future": 24}
    if counts != expected:
        raise ValidationError(f"bucket counts must be {expected}, got {dict(counts)}")
    return cases


def load_fixtures(root: Path, version: int = 1) -> tuple[Any, list[dict[str, Any]]]:
    data = root / "data"
    catalog_id, catalog = load_catalog(
        data / f"catalog-v{version}.json",
        require_primary=version >= 2,
    )
    return catalog, load_cases(data / f"dataset-v{version}.jsonl", catalog_id, catalog)
