"""Validate/build the candidate without using real credentials or uploading firmware."""
from __future__ import annotations

import argparse
import base64
import importlib.util
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compile", action="store_true", help="Compile firmware; never upload")
    args = parser.parse_args()
    if importlib.util.find_spec("esphome") is None:
        parser.error("Install first: python -m pip install -r esphome/requirements.txt")
    source = Path(__file__).resolve().parent / "respeaker-lite.yaml"
    work = Path(tempfile.mkdtemp(prefix="aw-")).resolve()
    config = work / source.name
    # Short build path prevents deeply nested Xtensa/CMake paths on Windows.
    config.write_text(
        source.read_text(encoding="utf-8").replace(
            "esphome:\n", "esphome:\n  build_path: b\n", 1
        ), encoding="utf-8"
    )
    # Disposable validation identity. Never reads or prints production secrets.
    key = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    (work / "secrets.yaml").write_text(
        "wifi_ssid: validation-only\nwifi_password: validation-only-password\n"
        f"api_encryption_key: {key}\n", encoding="utf-8"
    )
    print(f"Validation directory: {work}", flush=True)
    command = [sys.executable, "-m", "esphome", "compile"]
    if not args.compile:
        command.append("--only-generate")
    command.append(str(config))
    result = subprocess.run(command, check=False)
    print("No upload/flash command was executed.", flush=True)
    print("Temporary build directory retained for inspection.", flush=True)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
