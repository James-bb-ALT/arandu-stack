"""Generate deterministic Phase B corpus."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from .common import CORPUS_PATH, cases, write_corpus
except ImportError:
    from common import CORPUS_PATH, cases, write_corpus


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = "".join(__import__("json").dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for item in cases())
    if args.check:
        if not CORPUS_PATH.exists() or CORPUS_PATH.read_text(encoding="utf-8") != expected:
            raise SystemExit("Phase B corpus is not reproducible")
        print("phase-b corpus verified")
    else:
        write_corpus()
        print(f"phase-b corpus generated: {CORPUS_PATH}")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    main()
