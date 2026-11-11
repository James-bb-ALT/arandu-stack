"""Independent validator for v2-interpretation evidence (shapes only)."""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "evaluation" / "ptbr-independent" / "v2-interpret"
SNAPSHOT = json.loads((BASE / "snapshot-v1.json").read_text(encoding="utf-8"))

ALLOWED = {"plan", "ambiguous", "no_match"}
ACTIONS = {"get_state", "set_fan_percentage", "turn_off", "turn_on"}


def fail(message): raise ValueError(message)


def valid_identifier(value) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 128
        and all(c.isascii() and (c.isalnum() or c in "_-") for c in value)
    )


def validate():
    registry = {e["registry_id"] for e in SNAPSHOT["entities"]}
    rows = []
    for line in (BASE / "corpus-v1.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        status = row["expected"]["status"]
        if status not in ALLOWED:
            fail(f"{row['id']}: invalid status")
        if row["snapshot_id"] != SNAPSHOT["catalog_id"]:
            fail(f"{row['id']}: snapshot mismatch")
        if row["generation"] not in {"gen-001", "gen-000"}:
            fail(f"{row['id']}: generation")
        if not isinstance(row["text"], str):
            fail(f"{row['id']}: text type")
        if status in ("ambiguous", "no_match"):
            if "operations" in row["expected"]:
                fail(f"{row['id']}: abstention must not publish operations")
        else:
            operations = row["expected"]["operations"]
            if not 1 <= len(operations) <= 4:
                fail(f"{row['id']}: operation count")
            for operation in operations:
                if operation["action"] not in ACTIONS:
                    fail(f"{row['id']}: action")
                percentage = operation.get("percentage")
                if operation["action"] == "set_fan_percentage":
                    if not isinstance(percentage, int) or not 0 <= percentage <= 100:
                        fail(f"{row['id']}: percentage")
                elif "percentage" in operation:
                    fail(f"{row['id']}: stray percentage")
                targets = operation["targets"]
                if (
                    not isinstance(targets, list)
                    or not 1 <= len(targets) <= 32
                    or any(not valid_identifier(t) for t in targets)
                    or targets != sorted(set(targets))
                    or any(t not in registry for t in targets)
                ):
                    fail(f"{row['id']}: targets")
        rows.append(row)
    ids = [r["id"] for r in rows]
    if len(ids) != len(set(ids)):
        fail("duplicate case id")
    required = {"kind", "action", "match", "ambiguity", "operation", "evidence"}
    dimensions = {d for r in rows for d in r["dimensions"]}
    if not required.issubset(dimensions):
        fail("coverage dimensions incomplete")
    return rows


if __name__ == "__main__":
    rows = validate()
    print(json.dumps({"cases": len(rows),
                      "outcomes": {k: sum(r["expected"]["status"] == k for r in rows) for k in sorted(ALLOWED)}}, sort_keys=True))
