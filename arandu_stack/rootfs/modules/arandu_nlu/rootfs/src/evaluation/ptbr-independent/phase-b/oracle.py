"""Independent Phase B oracle and corpus validator."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parents[1]))
try:
    from ..semantic import CatalogIndex, ValidationError, compare_case, validate_catalog, validate_case
except ImportError:
    from semantic import CatalogIndex, ValidationError, compare_case, validate_catalog, validate_case
try:
    from .common import CATALOG_PATH, CORPUS_PATH
except ImportError:
    from common import CATALOG_PATH, CORPUS_PATH


def load() -> tuple[CatalogIndex, list[dict[str, Any]]]:
    catalog = validate_catalog(json.loads(CATALOG_PATH.read_text(encoding="utf-8")), require_primary=True)
    rows = [json.loads(line) for line in CORPUS_PATH.read_text(encoding="utf-8").splitlines() if line]
    for row in rows:
        validate_case(row, "phase-b-catalog-v1", catalog)
    return catalog, rows


def compare(rows: list[dict[str, Any]], catalog: CatalogIndex, actual: dict[str, Any] | None = None) -> dict[str, int]:
    counts = Counter(row["bucket"] for row in rows)
    if actual is not None:
        for row in rows:
            result = compare_case(row["expected"], actual[row["id"]], catalog)
            if result.classification != "exact_pass":
                raise ValidationError(f"{row['id']}: {result.classification}: {result.detail}")
    return dict(sorted(counts.items()))


def main() -> None:
    catalog, rows = load()
    ids = [row["id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise SystemExit("duplicate Phase B case id")
    print(json.dumps({"cases": len(rows), "buckets": compare(rows, catalog)}, sort_keys=True))


if __name__ == "__main__":
    main()
