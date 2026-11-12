"""Record pre-implementation entity-resolution capability absence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "evaluation" / "ptbr-independent" / "entity-resolution"
DESTINATION = BASE / "baselines" / "pre-implementation-v2.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    if DESTINATION.exists():
        raise SystemExit(f"refusing to overwrite pre-implementation baseline: {DESTINATION}")
    files = ["spec-v1.json", "catalog-v1.json", "corpus-v1.jsonl", "generate.py", "oracle.py", "freeze.py"]
    rows = [json.loads(line) for line in (BASE / "corpus-v1.jsonl").read_text(encoding="utf-8").splitlines() if line]
    value = {
        "schema_version": 1,
        "baseline_id": "ptbr-independent-entity-resolution-pre-implementation-v2",
        "status": "not_implemented",
        "product_output_used": False,
        "product_changes": [],
        "capabilities_absent": [
            "entity-resolution-request-binding",
            "external-entity-id-resolution",
            "explicit-alias-resolution",
            "constrained-display-name-resolution",
            "ambiguity-outcome",
            "stale-generation-rejection",
            "validated-mention-spans",
        ],
        "known_contract_gaps": [
            "no runtime result schema for entity candidates",
            "no independent product runner",
            "no Rust implementation or focused tests",
        ],
        "corpus": {"cases": len(rows), "outcomes": {key: sum(row["expected"]["outcome"] == key for row in rows) for key in ("ambiguous", "no_match", "resolved")}},
        "inputs": {name: {"path": f"evaluation/ptbr-independent/entity-resolution/{name}", "sha256": digest(BASE / name)} for name in files},
    }
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    DESTINATION.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(f"pre-implementation baseline created: {DESTINATION}")


if __name__ == "__main__":
    main()
