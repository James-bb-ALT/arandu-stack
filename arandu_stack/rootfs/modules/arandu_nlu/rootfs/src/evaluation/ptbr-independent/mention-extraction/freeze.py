"""Create and verify immutable mention-extraction input freeze."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "evaluation" / "ptbr-independent" / "mention-extraction"
MANIFEST = BASE / "freeze-v2.json"
FILES = ["snapshot-v1.json", "corpus-v1.jsonl", "generate.py", "oracle.py", "freeze.py", "prebaseline.py"]


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def build():
    rows = [json.loads(line) for line in (BASE / "corpus-v1.jsonl").read_text(encoding="utf-8").splitlines() if line]
    return {"schema_version": 1, "freeze_id": "ptbr-independent-mention-extraction-v2",
            "purpose": "prepared mention-extraction evidence before product implementation",
            "provenance": {"kind": "PROJECT_AUTHORED_SYNTHETIC", "license": "Apache-2.0", "product_output_used_for_labels": False},
            "files": {name: {"path": f"evaluation/ptbr-independent/mention-extraction/{name}", "sha256": digest(BASE / name)} for name in FILES},
            "corpus": {"line_count": len(rows),
                       "outcomes": {k: sum(r["expected"]["outcome"] == k for r in rows) for k in ("extracted", "no_extraction")}}}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--check", action="store_true")
    p.add_argument("--replace", action="store_true")
    a = p.parse_args()
    value = build()
    if a.check:
        if not MANIFEST.exists() or json.loads(MANIFEST.read_text(encoding="utf-8")) != value:
            raise SystemExit("mention-extraction freeze drift")
        print("mention-extraction freeze verified")
        return
    if MANIFEST.exists() and not a.replace:
        raise SystemExit("refusing to overwrite mention-extraction freeze")
    MANIFEST.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(f"mention-extraction freeze created: {MANIFEST}")


if __name__ == "__main__":
    main()
