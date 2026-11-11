"""Create and verify the immutable Phase A candidate manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from phase_a import cases

ROOT = Path(__file__).resolve().parent
DATASET = ROOT / "data" / "dataset-phase-a.jsonl"
MANIFEST = ROOT / "data" / "freeze-phase-a.json"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_manifest() -> dict[str, Any]:
    items = cases()
    return {
        "schema_version": 1,
        "freeze_id": "ptbr-independent-phase-a",
        "purpose": "Phase A candidate corpus before product baseline",
        "dataset": {
            "path": "evaluation/ptbr-independent/data/dataset-phase-a.jsonl",
            "sha256": sha256_file(DATASET),
            "line_count": len(items),
            "ids_sha256": hashlib.sha256("\n".join(item["id"] for item in items).encode()).hexdigest(),
        },
        "data_authorship": {
            "kind": "PROJECT_AUTHORED_SYNTHETIC",
            "license": "Apache-2.0",
            "product_output_used_for_labels": False,
        },
    }


def verify_manifest(manifest: dict[str, Any]) -> None:
    fresh = build_manifest()
    for key in ("schema_version", "freeze_id", "dataset", "data_authorship"):
        if manifest.get(key) != fresh[key]:
            raise ValueError(f"Phase A manifest drift in {key}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    if arguments.check:
        verify_manifest(json.loads(MANIFEST.read_text(encoding="utf-8")))
        print("Phase A manifest verified")
        return
    if MANIFEST.exists():
        raise SystemExit(f"refusing to overwrite existing manifest: {MANIFEST}")
    MANIFEST.write_text(json.dumps(build_manifest(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Phase A manifest created: {MANIFEST}")


if __name__ == "__main__":
    main()
