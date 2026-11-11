"""Independent post-implementation evaluation for mention extraction.

Gates (all must pass, no gold label is ever rewritten here):
1. preparation freeze v2 verifies, oracle validates, generator reproduces;
2. Rust extraction tests pass (lib + gold corpus + permutation).

Then records `baselines/post-implementation-v1.json` (refuses to overwrite).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "evaluation" / "ptbr-independent" / "mention-extraction"
DESTINATION = BASE / "baselines" / "post-implementation-v1.json"
CARGO = str(Path(r"C:\Users\victo\.rustup\toolchains\1.98.0-x86_64-pc-windows-msvc\bin") / "cargo.exe")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, capture_output=True, text=True, **kwargs)
    if completed.returncode != 0:
        raise SystemExit(f"command failed: {' '.join(command)}\n{completed.stderr[-2000:]}")
    return completed


def main() -> None:
    if DESTINATION.exists():
        raise SystemExit(f"refusing to overwrite post-implementation baseline: {DESTINATION}")

    run([sys.executable, str(BASE / "freeze.py"), "--check"])
    run([sys.executable, str(BASE / "generate.py"), "--check"])
    oracle = run([sys.executable, str(BASE / "oracle.py")])
    print(oracle.stdout.strip())
    run([CARGO, "test", "--workspace", "--locked", "--offline", "--lib"], cwd=ROOT)
    run([CARGO, "test", "--workspace", "--locked", "--offline", "--test", "extraction"], cwd=ROOT)

    rows = [json.loads(line) for line in (BASE / "corpus-v1.jsonl").read_text(encoding="utf-8").splitlines() if line]
    outcomes: dict[str, int] = {}
    for row in rows:
        key = row["expected"]["outcome"]
        outcomes[key] = outcomes.get(key, 0) + 1
    freeze_manifest = BASE / "freeze-v2.json"
    files = ["snapshot-v1.json", "corpus-v1.jsonl", "generate.py", "oracle.py", "freeze.py", "evaluate_mx.py"]
    value = {
        "schema_version": 1,
        "baseline_id": "ptbr-independent-mention-extraction-post-implementation-v1",
        "status": "implemented_tested_baselined",
        "active": False,
        "activation_note": "extractor is additive library API; no caller, no endpoint, no execution wiring",
        "product_output_used": False,
        "product_changes": [
            "addon/engine/src/extraction.rs",
            "addon/engine/src/normalize.rs",
            "addon/engine/src/parser.rs",
            "addon/engine/src/model.rs",
            "addon/engine/src/resolution.rs",
            "addon/engine/src/lib.rs",
            "addon/engine/tests/extraction.rs",
            "evaluation/ptbr-independent/mention-extraction/evaluate_mx.py",
        ],
        "conformance": {
            "cases": len(rows),
            "gold_outcomes": {key: outcomes.get(key, 0) for key in ("extracted", "no_extraction")},
            "gold_mismatches": 0,
            "rust_unit_tests": "cargo test --workspace --locked --offline --lib: pass",
            "rust_gold_tests": "cargo test --workspace --locked --offline --test extraction (30 gold rows + snapshot permutation): pass",
        },
        "inputs": {name: {"path": f"evaluation/ptbr-independent/mention-extraction/{name}", "sha256": digest(BASE / name)} for name in files},
        "freeze_manifest": {"path": "evaluation/ptbr-independent/mention-extraction/freeze-v2.json", "sha256": digest(freeze_manifest)},
    }
    DESTINATION.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(f"post-implementation baseline created: {DESTINATION}")


if __name__ == "__main__":
    main()
