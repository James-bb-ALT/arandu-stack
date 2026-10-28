#!/usr/bin/env python3
"""Generate the publishable Home Assistant add-on store repository.

Copies exactly the files the add-on Dockerfile consumes, plus store
metadata, into an isolated output directory. Verifies that every COPY
source in the Dockerfile exists in the generated package. Prints a
file manifest with SHA-256 digests. Creates nothing outside --output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from distribution import check, content

ROOT = Path(__file__).resolve().parent.parent
ADDON = ROOT / "addon"
DEFAULT_OUTPUT = ROOT / "target" / "dist" / "hass-store"

# Exact inputs consumed by addon/Dockerfile (COPY sources) plus the
# add-on recipe itself. Anything missing here fails the build.
REQUIRED_TOP = [
    "config.yaml",
    "Dockerfile",
    "DOCS.md",
    "CHANGELOG.md",
    "README.md",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    "container-inputs.json",
    "vendor-manifest.json",
]
REQUIRED_TREES = [".cargo", "engine", "vendor", "licenses"]

# Files inside engine/ without which `cargo build --locked` fails closed
# instead of resolving (a missing lockfile broke Supervisor installs).
REQUIRED_ENGINE_FILES = ["Cargo.toml", "Cargo.lock"]

STORE_NAME = "ARANDU NLU"
ADDON_DIR = "addon"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def dockerfile_copies() -> list[str]:
    """Local build-context sources of Dockerfile COPY steps.

    Skips flags (--from) and builder-internal absolute paths (/build),
    which never come from the published package.
    """
    sources: list[str] = []
    for line in (ADDON / "Dockerfile").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("COPY"):
            continue
        parts = stripped.split()
        operands = [part for part in parts[1:] if not part.startswith("--")]
        sources.extend(part for part in operands[:-1] if not part.startswith("/"))
    return sources


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--store-url", default="https://github.com/jaimevictor/Arandu-NLU")
    parser.add_argument("--maintainer", default="ARANDU NLU")
    arguments = parser.parse_args()
    check(ROOT)

    for name in REQUIRED_TOP:
        if not (ADDON / name).is_file():
            raise SystemExit(f"missing add-on input: {name}")
    for name in REQUIRED_TREES:
        if not (ADDON / name).is_dir():
            raise SystemExit(f"missing add-on tree: {name}")
    for name in REQUIRED_ENGINE_FILES:
        if not (ADDON / "engine" / name).is_file():
            raise SystemExit(
                f"missing engine input (locked builds fail without it): {name}"
            )
    for source in dockerfile_copies():
        if not (ADDON / source).exists():
            raise SystemExit(f"Dockerfile COPY source missing: {source}")

    output: Path = arguments.output
    if output.exists() and any(output.iterdir()):
        raise SystemExit('output must be empty; existing files are preserved')
    addon_dir = output / ADDON_DIR
    addon_dir.mkdir(parents=True)
    for name in REQUIRED_TOP:
        (addon_dir / name).write_bytes(content(ADDON / name))
    for name in REQUIRED_TREES:
        shutil.copytree(
            ADDON / name,
            addon_dir / name,
            copy_function=shutil.copyfile,
            ignore=shutil.ignore_patterns("__pycache__", "target", ".DS_Store"),
        )
    (output / "repository.yaml").write_text(
        f"# Home Assistant add-on store generated from the Arandu NLU monorepo.\n"
        f"name: {STORE_NAME}\n"
        f"url: {arguments.store_url}\n"
        f"maintainer: {arguments.maintainer}\n",
        encoding="utf-8",
    )
    (output / "README.md").write_text(
        "# Arandu NLU add-on store\n\n"
        "Add this repository URL in Home Assistant under Settings → "
        "Add-ons → Add-on Store → ⋮ → Repositories.\n\n"
        "Contents: one add-on directory per store layout requirements.\n\n"
        "- `addon/` — Local PT-BR NLU add-on (config, recipe, engine "
        "sources, vendored crates). Built locally by the Supervisor; no "
        "remote image is referenced.\n",
        encoding="utf-8",
    )
    manifest = []
    for path in sorted(output.rglob("*")):
        if path.is_file():
            manifest.append(
                {
                    "path": path.relative_to(output).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    (output / "manifest-sha256.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    engine_files = sum(1 for item in manifest if item["path"].startswith("addon/"))
    print(f"store generated: {output} ({engine_files} files under addon/)")


if __name__ == "__main__":
    main()
