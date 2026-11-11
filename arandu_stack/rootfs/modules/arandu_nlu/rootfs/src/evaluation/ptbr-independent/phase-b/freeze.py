"""Create and verify Phase B input freeze."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

try:
    from .common import CATALOG_PATH, CORPUS_PATH, ROOT, SPEC_PATH, cases
except ImportError:
    from common import CATALOG_PATH, CORPUS_PATH, ROOT, SPEC_PATH, cases

MANIFEST = ROOT / "evaluation" / "ptbr-independent" / "phase-b" / "freeze-v1.json"

def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def build() -> dict:
    items = cases()
    return {"schema_version": 1, "freeze_id": "ptbr-independent-phase-b-v1", "purpose": "frozen Phase B inputs before product requests", "provenance": {"kind": "PROJECT_AUTHORED_SYNTHETIC", "license": "Apache-2.0", "product_output_used_for_labels": False}, "spec": {"path": str(SPEC_PATH.relative_to(ROOT)).replace("\\", "/"), "sha256": digest(SPEC_PATH)}, "catalog": {"path": str(CATALOG_PATH.relative_to(ROOT)).replace("\\", "/"), "sha256": digest(CATALOG_PATH)}, "corpus": {"path": str(CORPUS_PATH.relative_to(ROOT)).replace("\\", "/"), "sha256": digest(CORPUS_PATH), "line_count": len(items), "buckets": {key: sum(item["bucket"] == key for item in items) for key in sorted({item["bucket"] for item in items})}}, "generator": {"path": "evaluation/ptbr-independent/phase-b/generate.py"}}

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    if args.check:
        if not MANIFEST.exists() or json.loads(MANIFEST.read_text(encoding="utf-8")) != build():
            raise SystemExit("Phase B freeze drift")
        print("phase-b freeze verified")
        return
    if MANIFEST.exists() and not args.replace:
        raise SystemExit("refusing to overwrite Phase B freeze; pass --replace after preserving prior manifest")
    MANIFEST.write_text(json.dumps(build(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(f"phase-b freeze created: {MANIFEST}")

if __name__ == "__main__":
    main()
