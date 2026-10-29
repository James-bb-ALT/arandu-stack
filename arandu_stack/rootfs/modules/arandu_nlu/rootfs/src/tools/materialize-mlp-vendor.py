#!/usr/bin/env python3
"""Materialize and verify exact crates.io archives for the MLP build."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tarfile
import tempfile
import tomllib


ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "addon" / "vendor"
MANIFEST = ROOT / "addon" / "vendor-manifest.json"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def locked_packages() -> list[dict[str, str]]:
    lock = tomllib.loads((ROOT / "Cargo.lock").read_text(encoding="utf-8"))
    return sorted(
        (
            {
                "name": package["name"],
                "version": package["version"],
                "checksum": package["checksum"],
            }
            for package in lock["package"]
            if package.get("source", "").startswith("registry+")
        ),
        key=lambda package: (package["name"], package["version"]),
    )


def safe_members(archive: bytes, package_dir: str) -> list[tarfile.TarInfo]:
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        members = tar.getmembers()
    if not members:
        raise ValueError(f"{package_dir}: empty archive")
    for member in members:
        path = PurePosixPath(member.name)
        if (
            path.is_absolute()
            or ".." in path.parts
            or not path.parts
            or path.parts[0] != package_dir
            or not (member.isdir() or member.isfile())
        ):
            raise ValueError(f"{package_dir}: unsafe archive member {member.name}")
    return members


def extract_archive(archive: bytes, destination: Path, package_dir: str) -> None:
    members = safe_members(archive, package_dir)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for member in members:
            relative = PurePosixPath(member.name).relative_to(package_dir)
            target = destination.joinpath(*relative.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = tar.extractfile(member)
            if source is None:
                raise ValueError(f"{package_dir}: unreadable {member.name}")
            target.write_bytes(source.read())
            target.chmod(member.mode & 0o777)


def cargo_checksum(package: Path, archive_sha256: str) -> None:
    files = {
        path.relative_to(package).as_posix(): digest(path.read_bytes())
        for path in sorted(package.rglob("*"))
        if path.is_file() and path.name != ".cargo-checksum.json"
    }
    value = {
        "files": files,
        "package": archive_sha256,
    }
    (package / ".cargo-checksum.json").write_text(
        json.dumps(value, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )


def tree_record(package: Path) -> dict[str, int | str]:
    tree = hashlib.sha256()
    count = 0
    total = 0
    for path in sorted(package.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(package).as_posix()
        data = path.read_bytes()
        mode = stat.S_IMODE(path.stat().st_mode)
        row = (
            f"{relative}\0{mode:o}\0{len(data)}\0{digest(data)}\n"
        ).encode("utf-8")
        tree.update(row)
        count += 1
        total += len(data)
    return {
        "file_count": count,
        "total_bytes": total,
        "tree_sha256": tree.hexdigest(),
    }


def materialize(cache: Path) -> None:
    packages = locked_packages()
    temporary_root = Path(tempfile.mkdtemp(prefix="local-nlu-vendor-"))
    temporary_vendor = temporary_root / "vendor"
    temporary_vendor.mkdir()
    records: list[dict[str, object]] = []
    try:
        for package in packages:
            package_dir = f"{package['name']}-{package['version']}"
            archive_path = cache / f"{package_dir}.crate"
            archive = archive_path.read_bytes()
            archive_sha256 = digest(archive)
            if archive_sha256 != package["checksum"]:
                raise ValueError(f"{package_dir}: archive checksum")
            destination = temporary_vendor / package_dir
            destination.mkdir()
            extract_archive(archive, destination, package_dir)
            cargo_checksum(destination, archive_sha256)
            records.append(
                {
                    "archive_sha256": archive_sha256,
                    "generated_files": [".cargo-checksum.json"],
                    "name": package["name"],
                    **tree_record(destination),
                    "version": package["version"],
                }
            )
        manifest = {
            "packages": records,
            "schema_version": 1,
            "source": "verified crates.io .crate archives",
            "transformation": (
                "safe regular-file extraction plus deterministic "
                ".cargo-checksum.json generation"
            ),
        }
        temporary_manifest = temporary_root / "vendor-manifest.json"
        temporary_manifest.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if VENDOR.exists():
            shutil.rmtree(VENDOR)
        os.replace(temporary_vendor, VENDOR)
        os.replace(temporary_manifest, MANIFEST)
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)


def check() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    expected = {
        (package["name"], package["version"]): package
        for package in manifest["packages"]
    }
    locked = {
        (package["name"], package["version"]): package
        for package in locked_packages()
    }
    if set(expected) != set(locked):
        raise ValueError("vendor package set")
    actual_dirs = {path.name for path in VENDOR.iterdir() if path.is_dir()}
    expected_dirs = {f"{name}-{version}" for name, version in expected}
    if actual_dirs != expected_dirs:
        raise ValueError("vendor directory set")
    for key, record in expected.items():
        locked_package = locked[key]
        if record["archive_sha256"] != locked_package["checksum"]:
            raise ValueError(f"{key}: archive identity")
        package = VENDOR / f"{key[0]}-{key[1]}"
        actual = tree_record(package)
        for field in ("file_count", "total_bytes", "tree_sha256"):
            if actual[field] != record[field]:
                raise ValueError(f"{key}: {field}")
        checksum = json.loads(
            (package / ".cargo-checksum.json").read_text(encoding="utf-8")
        )
        if checksum.get("package") != locked_package["checksum"]:
            raise ValueError(f"{key}: Cargo package checksum")
        files = checksum.get("files")
        if type(files) is not dict:
            raise ValueError(f"{key}: Cargo file checksums")
        actual_files = {
            path.relative_to(package).as_posix(): digest(path.read_bytes())
            for path in sorted(package.rglob("*"))
            if path.is_file() and path.name != ".cargo-checksum.json"
        }
        if files != actual_files:
            raise ValueError(f"{key}: Cargo file checksums")
    print("MLP exact vendor PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--from-cache", type=Path)
    group.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    if arguments.check:
        check()
    else:
        materialize(arguments.from_cache)
        check()


if __name__ == "__main__":
    main()
