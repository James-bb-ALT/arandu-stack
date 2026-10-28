#!/usr/bin/env python3
"""FIXTURE_TECNICA black-box smoke test of the release binary."""

from __future__ import annotations

import http.client
import json
from pathlib import Path
import socket
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "target" / "release" / "local-nlu"


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def request(port: int, method: str, path: str, body: bytes | None = None) -> object:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    headers = {"Content-Type": "application/json"} if body is not None else {}
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    payload = response.read()
    connection.close()
    if response.status != 200:
        raise RuntimeError(f"HTTP {response.status}")
    return json.loads(payload)


def main() -> None:
    if not BINARY.is_file():
        raise SystemExit("release binary missing")
    port = free_port()
    process = subprocess.Popen(
        [
            str(BINARY),
            "serve",
            "--listen",
            f"127.0.0.1:{port}",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    smoke_error: Exception | None = None
    try:
        for _ in range(200):
            if process.poll() is not None:
                raise RuntimeError("server exited")
            try:
                health = request(port, "GET", "/health")
            except (ConnectionError, OSError):
                time.sleep(0.025)
                continue
            if health != {"status": "ok", "version": 1}:
                raise RuntimeError("health response")
            break
        else:
            raise RuntimeError("health timeout")

        catalog = json.loads(
            (
                ROOT
                / "data/mlp/project-authored-synthetic-catalog-v1.json"
            ).read_text(encoding="utf-8")
        )
        cases = [
            json.loads(line)
            for line in (
                ROOT / "data/mlp/project-authored-synthetic-v1.jsonl"
            ).read_text(encoding="utf-8").splitlines()
        ]
        case = next(row for row in cases if row["id"] == "ordered-mixed-actions")
        payload = json.dumps(
            {
                "catalog": catalog,
                "text": case["text"],
                "version": 1,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        actual = request(port, "POST", "/v1/interpret", payload)
        if actual != case["expected"]:
            raise RuntimeError(f"interpret mismatch: {actual!r}")
    except Exception as error:
        smoke_error = error
    finally:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
    if smoke_error is not None:
        stderr = process.stderr.read().strip() if process.stderr is not None else ""
        detail = f"{smoke_error}"
        if stderr:
            detail = f"{detail}; server stderr: {stderr}"
        raise RuntimeError(detail) from smoke_error
    print("MLP release binary smoke PASS")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"MLP smoke failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
