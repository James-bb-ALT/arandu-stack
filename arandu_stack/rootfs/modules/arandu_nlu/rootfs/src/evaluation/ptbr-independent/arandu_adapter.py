"""Local-only HTTP v1 adapter for independent evaluation."""
from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import subprocess
import time
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

MAX_BYTES = 65_536
TIMEOUT_SECONDS = 3.0
HEALTH = {"status": "ok", "version": 1}


class AdapterError(RuntimeError):
    """Raised for a failed HTTP exchange, not a product rejection DTO."""


@dataclass
class LocalServer:
    process: subprocess.Popen[str]
    endpoint: str

    def memory_bytes(self) -> dict[str, int | None]:
        """Return VmRSS and VmHWM for this server PID from Linux procfs."""
        try:
            status = Path(f"/proc/{self.process.pid}/status").read_text(encoding="ascii")
        except (OSError, UnicodeError):
            return {"rss_bytes": None, "hwm_bytes": None}
        values: dict[str, int | None] = {}
        for field, key in (("VmRSS", "rss_bytes"), ("VmHWM", "hwm_bytes")):
            match = re.search(rf"^{field}:\s+(\d+)\s+kB$", status, re.MULTILINE)
            values[key] = int(match.group(1)) * 1024 if match else None
        return values

    def rss_bytes(self) -> int | None:
        """Return current RSS for compatibility with existing callers."""
        return self.memory_bytes()["rss_bytes"]

    def close(self) -> None:
        if self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=2)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def local_endpoint(endpoint: str) -> tuple[str, int]:
    parsed = urlsplit(endpoint)
    if parsed.scheme != "http" or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise AdapterError("endpoint must be an origin-only http URL")
    if parsed.username or parsed.password or parsed.hostname is None:
        raise AdapterError("endpoint must have one local host and no credentials")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 80, type=socket.SOCK_STREAM)
    except OSError as error:
        raise AdapterError(f"endpoint DNS lookup failed: {error}") from error
    if not addresses:
        raise AdapterError("endpoint did not resolve")
    for _, _, _, _, sockaddr in addresses:
        if not ipaddress.ip_address(sockaddr[0]).is_loopback:
            raise AdapterError("endpoint must resolve only to loopback")
    return parsed.hostname, parsed.port or 80


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def decode_response_json(payload: bytes) -> Any:
    """Decode response JSON without permissive Python JSON extensions."""
    try:
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicates,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise AdapterError(f"response body is not valid strict JSON: {error}") from error


def _request(endpoint: str, method: str, path: str, body: bytes | None = None) -> Any:
    host, port = local_endpoint(endpoint)
    headers = {"Accept": "application/json", "Connection": "close"}
    if body is not None:
        if len(body) > MAX_BYTES:
            raise AdapterError("request body exceeds HTTP v1 limit")
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(body))
    connection = http.client.HTTPConnection(host, port, timeout=TIMEOUT_SECONDS)
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        payload = response.read(MAX_BYTES + 1)
    except (OSError, http.client.HTTPException, TimeoutError) as error:
        raise AdapterError(f"HTTP exchange failed: {error}") from error
    finally:
        connection.close()
    if len(payload) > MAX_BYTES:
        raise AdapterError("response body exceeds HTTP v1 limit")
    if response.status != 200:
        raise AdapterError(f"unexpected HTTP status: {response.status}")
    content_type = response.getheader("Content-Type", "")
    if not content_type.lower().startswith("application/json"):
        raise AdapterError("response Content-Type is not application/json")
    return decode_response_json(payload)


def health(endpoint: str) -> None:
    if _request(endpoint, "GET", "/health") != HEALTH:
        raise AdapterError("health response differs from HTTP v1 contract")


def interpret(endpoint: str, text: str, catalog: Any) -> Any:
    body = json.dumps(
        {"catalog": catalog, "text": text, "version": 1},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return _request(endpoint, "POST", "/v1/interpret", body)


def start_local(binary: Path, startup_timeout_seconds: float = 5.0) -> LocalServer:
    if not binary.is_file():
        raise AdapterError(f"release binary is missing: {binary}")
    port = free_port()
    server = LocalServer(
        process=subprocess.Popen(
            [str(binary), "serve", "--listen", f"127.0.0.1:{port}"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        ),
        endpoint=f"http://127.0.0.1:{port}",
    )
    deadline = time.monotonic() + startup_timeout_seconds
    try:
        while time.monotonic() < deadline:
            if server.process.poll() is not None:
                stderr = server.process.stderr.read().strip() if server.process.stderr else ""
                raise AdapterError(f"server exited during startup: {stderr}")
            try:
                health(server.endpoint)
                return server
            except AdapterError:
                time.sleep(0.025)
        raise AdapterError("health readiness timed out")
    except Exception:
        server.close()
        raise
