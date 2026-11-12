"""Create and verify immutable input manifest before product HTTP evaluation."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from corpus import load_fixtures

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
MANIFEST = DATA / "freeze-v2.json"
REPOSITORY = ROOT.parent.parent


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_digest(path: Path) -> tuple[str, list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    ignored_names = {"__pycache__", ".DS_Store", "__MACOSX"}
    for file_path in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
        relative_path = file_path.relative_to(path)
        if any(part in ignored_names or part.endswith(".pyc") for part in relative_path.parts):
            continue
        relative = file_path.relative_to(REPOSITORY).as_posix()
        entries.append({"path": relative, "sha256": sha256_file(file_path), "bytes": file_path.stat().st_size})
    payload = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest(), entries


def git_output(*arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def build_manifest() -> dict[str, Any]:
    catalog, cases = load_fixtures(ROOT, version=2)
    catalog_path = DATA / "catalog-v2.json"
    dataset_path = DATA / "dataset-v2.jsonl"
    engine_path = REPOSITORY / "addon" / "engine" / "src"
    evaluation_digest, evaluation_files = tree_digest(ROOT)
    engine_digest, engine_files = tree_digest(engine_path)
    ids = [case["id"] for case in cases]
    id_digest = hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()
    return {
        "schema_version": 1,
        "freeze_id": "ptbr-independent-v2",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "purpose": "frozen project-authored inputs before first product HTTP request",
        "data_authorship": {
            "kind": "PROJECT_AUTHORED_SYNTHETIC",
            "license": "Apache-2.0",
            "second_annotator": False,
            "product_output_used_for_labels": False,
        },
        "catalog": {"path": catalog_path.relative_to(REPOSITORY).as_posix(), "sha256": sha256_file(catalog_path), "bytes": catalog_path.stat().st_size, "target_count": len(catalog.all_targets)},
        "dataset": {
            "path": dataset_path.relative_to(REPOSITORY).as_posix(),
            "sha256": sha256_file(dataset_path),
            "bytes": dataset_path.stat().st_size,
            "line_count": len(cases),
            "id_sha256": id_digest,
            "buckets": dict(sorted(Counter(case["bucket"] for case in cases).items())),
        },
        "engine_source": {"path": "addon/engine/src", "sha256": engine_digest, "files": engine_files},
        "evaluation_source": {"path": "evaluation/ptbr-independent", "sha256": evaluation_digest, "files": evaluation_files},
        "repository": {"commit": git_output("rev-parse", "HEAD"), "tree": git_output("rev-parse", "HEAD^{tree}"), "status_porcelain": git_output("status", "--porcelain=v1")},
    }


def canonical_bytes(manifest: dict[str, Any]) -> bytes:
    return (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def verify_manifest(manifest: dict[str, Any]) -> None:
    fresh = build_manifest()
    for section in ("catalog", "dataset", "engine_source"):
        if manifest.get(section) != fresh[section]:
            raise ValueError(f"freeze drift in {section}")
    if manifest.get("data_authorship") != fresh["data_authorship"]:
        raise ValueError("freeze authorship declaration drift")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    if arguments.check:
        if not MANIFEST.exists():
            raise SystemExit(f"freeze manifest does not exist: {MANIFEST}")
        verify_manifest(json.loads(MANIFEST.read_text(encoding="utf-8")))
        print("ptbr-independent freeze verified")
        return
    if MANIFEST.exists():
        raise SystemExit(f"refusing to overwrite existing freeze manifest: {MANIFEST}")
    MANIFEST.write_bytes(canonical_bytes(build_manifest()))
    print(f"ptbr-independent freeze created: {MANIFEST}")


if __name__ == "__main__":
    main()
