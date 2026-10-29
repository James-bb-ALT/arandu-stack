#!/usr/bin/env python3
"""FIXTURE_TECNICA structural checks for the distributable MLP."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
import tomllib


ROOT = Path(__file__).resolve().parent.parent
ADDON = ROOT / "addon"
INTEGRATION = ROOT / "custom_components" / "local_nlu"

EXPECTED_INTEGRATION = {
    "__init__.py",
    "ai_lab.py",
    "ai_lab_core.py",
    "ai_lab_demo.py",
    "ai_lab_demo.json",
    "ai_lab_panel.js",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    "catalog.py",
    "client.py",
    "config_flow.py",
    "const.py",
    "conversation.py",
    "capabilities.py",
    "contextual_protocol.py",
    "contextual_catalog.py",
    "contextual_runtime.py",
    "contextual_errors.py",
    "device_location.py",
    "diagnostics.py",
    "identity.py",
    "voice_input.py",
    "queries.py",
    "manifest.json",
    "protocol.py",
    "runtime.py",
    "strings.json",
    "translations/en.json",
    "translations/pt-BR.json",
    "brand/icon.png",
}
EXPECTED_VENDOR = {
    "itoa-1.0.18",
    "memchr-2.8.3",
    "proc-macro2-1.0.107",
    "quote-1.0.47",
    "ryu-1.0.23",
    "serde-1.0.228",
    "serde_core-1.0.228",
    "serde_derive-1.0.228",
    "serde_json-1.0.145",
    "syn-2.0.119",
    "tinyvec-1.12.0",
    "tinyvec_macros-0.1.1",
    "unicode-ident-1.0.24",
    "unicode-normalization-0.1.25",
}
EXPECTED_LOCK_PACKAGES = {
    "itoa": "1.0.18",
    "local-nlu": tomllib.loads((ADDON / 'engine/Cargo.toml').read_text())['package']['version'],
    "memchr": "2.8.3",
    "proc-macro2": "1.0.107",
    "quote": "1.0.47",
    "ryu": "1.0.23",
    "serde": "1.0.228",
    "serde_core": "1.0.228",
    "serde_derive": "1.0.228",
    "serde_json": "1.0.145",
    "syn": "2.0.119",
    "tinyvec": "1.12.0",
    "tinyvec_macros": "0.1.1",
    "unicode-ident": "1.0.24",
    "unicode-normalization": "0.1.25",
}


def fail(message: str) -> None:
    raise SystemExit(f"MLP package check failed: {message}")


def integration_files() -> set[str]:
    return {
        path.relative_to(INTEGRATION).as_posix()
        for path in INTEGRATION.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }


def check_integration() -> None:
    actual = integration_files()
    if actual != EXPECTED_INTEGRATION:
        fail(f"integration file set differs: {sorted(actual ^ EXPECTED_INTEGRATION)}")
    manifest = json.loads((INTEGRATION / "manifest.json").read_text())
    expected = {
        "config_flow": True,
        "domain": "local_nlu",
        "iot_class": "local_polling",
        "requirements": [],
        "single_config_entry": True,
        "version": EXPECTED_LOCK_PACKAGES['local-nlu'],
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            fail(f"manifest {key}")
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(INTEGRATION.rglob("*.py"))
    ).lower()
    for forbidden in (
        "supervisor_token",
        "pairing",
        "noise_",
        "executionledger",
        "boto",
        "amazon",
        "aws_",
    ):
        if forbidden in source:
            fail(f"forbidden integration surface: {forbidden}")


def check_addon() -> None:
    required = {
        ".cargo/config.toml",
        "DOCS.md",
        "Dockerfile",
        "LICENSE",
        "README.md",
        "THIRD_PARTY_NOTICES.md",
        "config.yaml",
        "container-inputs.json",
        "engine/Cargo.lock",
        "engine/Cargo.toml",
        "vendor-manifest.json",
    }
    actual = {
        path.relative_to(ADDON).as_posix()
        for path in ADDON.rglob("*")
        if path.is_file()
    }
    if not required <= actual:
        fail(f"missing addon files: {sorted(required - actual)}")
    config = (ADDON / "config.yaml").read_text(encoding="utf-8")
    for required_line in (
        "slug: ptbr_nlu",
        "init: false",
        "schema: false",
    ):
        if required_line not in config:
            fail(f"addon config missing {required_line}")
    for forbidden in (
        "homeassistant_api:",
        "hassio_api:",
        "host_network:",
        "ingress:",
        "map:",
        "ports:",
    ):
        if forbidden in config:
            fail(f"addon authority surface: {forbidden}")
    dockerfile = (ADDON / "Dockerfile").read_text(encoding="utf-8")
    for required_text in (
        "docker.io/library/rust:1.98.0-alpine3.22@sha256:"
        "2e452153b2dc6bed8ef123c4801cfd3f637e7c092846f974ecdce5e64af3120c",
        "FROM scratch",
        "CARGO_PROFILE_RELEASE_STRIP=none",
        'io.hass.type="app"',
        "COPY LICENSE /licenses/Apache-2.0.txt",
        "COPY THIRD_PARTY_NOTICES.md /licenses/THIRD_PARTY_NOTICES.md",
        "COPY vendor/memchr-2.8.3/LICENSE-MIT /licenses/memchr-MIT.txt",
        "COPY vendor/unicode-ident-1.0.24/LICENSE-UNICODE "
        "/licenses/unicode-ident-Unicode-3.0.txt",
        "USER 65534:65534",
        '"0.0.0.0:11555"',
    ):
        if required_text not in dockerfile:
            fail(f"Dockerfile missing {required_text}")
    if re.search(r"\b(curl|wget|apk add|apt-get|pip install)\b", dockerfile):
        fail("Dockerfile performs undeclared network/package installation")
    if "ARG RUST_IMAGE" in dockerfile:
        fail("Dockerfile builder is overridable")
    container_inputs = json.loads(
        (ADDON / "container-inputs.json").read_text(encoding="utf-8")
    )
    if container_inputs != {
        "builder": {
            "image": "docker.io/library/rust",
            "license": "MIT",
            "oci_index_digest": (
                "sha256:"
                "2e452153b2dc6bed8ef123c4801cfd3f637e7c092846f974ecdce5e64af3120c"
            ),
            "platform_manifests": {
                "aarch64": (
                    "sha256:"
                    "f2d4d7fe1d065b8da883ed4aaa3a7e4b0a4e68e6915ddb1ed118627053526602"
                ),
                "amd64": (
                    "sha256:"
                    "e8081123c663fca08740af7dc09d936f198e62490d270b6e698699f1b541a31f"
                ),
            },
            "source": "https://github.com/rust-lang/docker-rust",
            "tag": "1.98.0-alpine3.22",
        },
        "recorded_on": "2026-09-12",
        "runtime": {
            "base": "scratch",
            "packages": [],
        },
        "schema_version": 1,
    }:
        fail("container input record")


def check_dependencies() -> None:
    subprocess.run(
        [sys.executable, str(ROOT / "tools/materialize-mlp-vendor.py"), "--check"],
        check=True,
    )
    if (ROOT / "Cargo.lock").read_bytes().replace(b"\r\n", b"\n") != (
        ADDON / "engine" / "Cargo.lock"
    ).read_bytes().replace(b"\r\n", b"\n"):
        fail("root and app lockfiles differ")
    lock = tomllib.loads((ROOT / "Cargo.lock").read_text(encoding="utf-8"))
    packages = {
        package["name"]: package["version"] for package in lock["package"]
    }
    if packages != EXPECTED_LOCK_PACKAGES:
        fail(f"lock package set differs: {packages}")
    vendor = {
        path.name for path in (ADDON / "vendor").iterdir() if path.is_dir()
    }
    if vendor != EXPECTED_VENDOR:
        fail(f"vendor set differs: {sorted(vendor ^ EXPECTED_VENDOR)}")
    for package in sorted(vendor):
        root = ADDON / "vendor" / package
        if not (root / ".cargo-checksum.json").is_file():
            fail(f"{package} lacks checksum manifest")
        licenses = [
            path
            for path in root.iterdir()
            if path.is_file()
            and (
                path.name.lower().startswith("license")
                or path.name.lower() in {"copying", "unlicense"}
            )
        ]
        if not licenses:
            fail(f"{package} lacks license text")


def check_licenses_and_limits() -> None:
    root_license = (ROOT / "LICENSE").read_bytes().replace(b"\r\n", b"\n")
    if (ADDON / "LICENSE").read_bytes().replace(b"\r\n", b"\n") != root_license:
        fail("app license differs from project license")
    if (INTEGRATION / "LICENSE").read_bytes().replace(b"\r\n", b"\n") != root_license:
        fail("integration license differs from project license")
    addon_notice = (ADDON / "THIRD_PARTY_NOTICES.md").read_text(
        encoding="utf-8"
    )
    for required in ("memchr", "MIT", "unicode-ident", "Unicode-3.0"):
        if required not in addon_notice:
            fail(f"app notice missing {required}")
    integration_notice = (
        INTEGRATION / "THIRD_PARTY_NOTICES.md"
    ).read_text(encoding="utf-8")
    normalized_notice = " ".join(integration_notice.lower().split())
    if "bundles no third-party python packages" not in normalized_notice:
        fail("integration notice")
    python_limits = (INTEGRATION / "const.py").read_text(encoding="utf-8")
    rust_limits = (ADDON / "engine/src/server.rs").read_text(encoding="utf-8")
    if "MAX_REQUEST_BYTES = 65_536" not in python_limits:
        fail("integration request limit")
    if "MAX_REQUEST_BYTES: usize = 65_536" not in rust_limits:
        fail("engine request limit")


def check_workspace() -> None:
    workspace = tomllib.loads((ROOT / "Cargo.toml").read_text(encoding="utf-8"))
    if workspace["workspace"]["members"] != ["addon/engine"]:
        fail("workspace is not the single active engine")
    engine = tomllib.loads(
        (ADDON / "engine" / "Cargo.toml").read_text(encoding="utf-8")
    )
    if set(engine["dependencies"]) != {
        "serde",
        "serde_json",
        "unicode-normalization",
    }:
        fail("direct dependency set differs")


def main() -> None:
    check_integration()
    check_addon()
    check_dependencies()
    check_licenses_and_limits()
    check_workspace()
    print("MLP package structure PASS")


if __name__ == "__main__":
    try:
        main()
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        fail(str(error))
