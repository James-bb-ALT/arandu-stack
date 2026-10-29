#!/usr/bin/env python3
"""FIXTURE_TECNICA: build add-on ZIP and test runtime imported from both Python ZIPs."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import tempfile
import zipfile

from hacs import ASSET, INTEGRATION_FILES, check_zip

ROOT = Path(__file__).resolve().parent.parent


def extract(package, destination, expected=None):
    checksum = package.with_suffix(package.suffix + ".sha256").read_text().split()[0]
    if hashlib.sha256(package.read_bytes()).hexdigest() != checksum:
        raise ValueError("package checksum differs")
    with zipfile.ZipFile(package) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or (expected is not None and set(names) != expected):
            raise ValueError("package file set differs")
        for info in archive.infolist():
            path = PurePosixPath(info.filename)
            mode = info.external_attr >> 16
            if path.is_absolute() or ".." in path.parts or "\\" in info.filename or ":" in info.filename or stat.S_ISLNK(mode):
                raise ValueError("unsafe package member")
        if archive.testzip() is not None:
            raise ValueError("invalid package CRC")
        archive.extractall(destination)


def main(packages, staging):
    version = json.loads((ROOT / "custom_components/local_nlu/manifest.json").read_text())["version"]
    addon = staging / "addon-package"
    extract(packages / f"arandu-nlu-addon-{version}.zip", addon)
    # Compile only the engine extracted from the add-on ZIP, including its vendor and lock.
    build = staging / "artifact-build"
    subprocess.run(["cargo", "build", "--manifest-path", "engine/Cargo.toml", "--target-dir", str(build),
                    "--release", "--locked", "--offline"], cwd=addon / "addon", check=True)
    binary = build / "release/local-nlu"
    for flat in (True, False):
        project = staging / ("flat-integration" if flat else "versioned-integration")
        project.mkdir()
        package = packages / (ASSET if flat else f"arandu-nlu-integration-{version}.zip")
        if flat:
            check_zip(package, version)
            directory = project / "custom_components/local_nlu"
            directory.mkdir(parents=True)
            extract(package, directory, set(INTEGRATION_FILES))
        else:
            extract(package, project, {"custom_components/local_nlu/" + name for name in INTEGRATION_FILES})
        code = """
import pathlib, sys, unittest
root = pathlib.Path.cwd().resolve()
suite = unittest.defaultTestLoader.discover(sys.argv[1], pattern='test_contextual_identity.py')
result = unittest.TextTestRunner(verbosity=1).run(suite)
for name, module in list(sys.modules.items()):
    if name.startswith('custom_components.local_nlu.'):
        assert pathlib.Path(module.__file__).resolve().is_relative_to(root), (name, module.__file__)
assert result.testsRun >= 18 and not result.skipped, (result.testsRun, result.skipped)
sys.exit(not result.wasSuccessful())
"""
        env = {**os.environ, "ARANDU_NLU_BINARY": str(binary), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(project)}
        subprocess.run([sys.executable, "-c", code, str(ROOT / "tests/mlp")], cwd=project, env=env, check=True)
        print(f"Extracted {package.name}: identity regression + real artifact Rust v4/executor PASS", flush=True)
    print(f"Artifact version {version}: add-on offline build and both installed integration ZIPs PASS", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--packages", required=True, type=Path)
    parser.add_argument("--staging", type=Path)
    args = parser.parse_args()
    if args.staging is not None:
        # Never delete or overwrite an existing staging directory.
        args.staging.mkdir(parents=True, exist_ok=False)
        main(args.packages.resolve(), args.staging.resolve())
    else:
        with tempfile.TemporaryDirectory(prefix="arandu-identity-artifact-") as staging:
            main(args.packages.resolve(), Path(staging))
