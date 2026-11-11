"""Generate the separate Phase A candidate corpus."""
from __future__ import annotations

import json
from pathlib import Path

from phase_a import cases

OUTPUT = Path(__file__).resolve().parent / "data" / "dataset-phase-a.jsonl"


def main() -> None:
    items = cases()
    OUTPUT.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for item in items) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(items)} Phase A cases to {OUTPUT}")


if __name__ == "__main__":
    main()
