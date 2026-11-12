"""Promote reviewed evaluation artifacts without overwrite."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
REPOSITORY = ROOT.parent.parent
DEFAULT_SOURCE = REPOSITORY / "target" / "ptbr-independent"


class PromotionSpec:
    def __init__(self, version: int) -> None:
        if version not in (1, 2):
            raise ValueError("supported baseline versions are 1 and 2")
        self.version = version
        self.suffix = f"v{version}"
        self.freeze_id = f"ptbr-independent-v{version}"
        self.destination = ROOT / "baselines" / self.suffix
        self.required = (
            f"run-{self.suffix}.json",
            f"run-{self.suffix}.md",
            f"benchmark-{self.suffix}.json",
            f"benchmark-{self.suffix}.md",
        )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"artifact must contain an object: {path}")
    return value


def validate(source: Path, version: int = 2) -> tuple[dict[str, Any], dict[str, Any]]:
    spec = PromotionSpec(version)
    missing = [name for name in spec.required if not (source / name).is_file()]
    if missing:
        raise ValueError(f"baseline artifacts missing: {', '.join(missing)}")
    run = load_json(source / f"run-{spec.suffix}.json")
    benchmark = load_json(source / f"benchmark-{spec.suffix}.json")
    if run.get("schema_version") != 1 or benchmark.get("schema_version") != 1:
        raise ValueError("unsupported evaluation artifact schema")
    if run.get("freeze_id") != spec.freeze_id or benchmark.get("freeze_id") != spec.freeze_id:
        raise ValueError("artifact freeze id mismatch")
    scored_failures = sum(
        1
        for case in run.get("cases", [])
        if case.get("bucket") != "known-gap-future" and case.get("classification") != "exact_pass"
    )
    if scored_failures:
        raise ValueError("run contains scored failures")
    if benchmark.get("summary", {}).get("failures", 0) or benchmark.get("summary", {}).get("cold_failures", 0):
        raise ValueError("benchmark contains failures")
    if "binary" not in run.get("hashes", {}) or "binary" not in benchmark.get("hashes", {}):
        raise ValueError("promotable baseline requires binary hashes")
    return run, benchmark


def promote(source: Path, destination: Path, version: int = 2) -> Path:
    spec = PromotionSpec(version)
    run, benchmark = validate(source, version)
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite baseline: {destination}")
    destination.mkdir(parents=True)
    for name in spec.required:
        shutil.copy2(source / name, destination / name)
    manifest = {
        "schema_version": 1,
        "freeze_id": spec.freeze_id,
        "artifacts": {name: sha256_file(destination / name) for name in spec.required},
        "run_hashes": run["hashes"],
        "benchmark_hashes": benchmark["hashes"],
    }
    (destination / f"manifest-{spec.suffix}.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return destination


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--version", type=int, choices=(1, 2), default=2)
    arguments = parser.parse_args()
    spec = PromotionSpec(arguments.version)
    destination = arguments.destination or spec.destination
    print(f"baseline promoted to {promote(arguments.source.resolve(), destination.resolve(), arguments.version)}")


if __name__ == "__main__":
    main()
