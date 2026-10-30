"""Bounded local HTTP client for the passive add-on."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
from collections.abc import Awaitable, Callable, Iterable
from typing import Any
from urllib.parse import SplitResult, urlsplit, urlunsplit

from .const import (
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    REQUEST_TIMEOUT_SECONDS,
)


class ClientError(Exception):
    """A fail-closed local transport or endpoint error."""


Resolver = Callable[[str, int], Awaitable[Iterable[str]]]

_ALLOWED_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
)


def _address_is_local(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(address in network for network in _ALLOWED_NETWORKS)


def _hostname_is_local(host: str) -> bool:
    labels = host.split(".")
    if any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or not all(
            character.isascii()
            and (character.isalnum() or character == "-")
            for character in label
        )
        for label in labels
    ):
        return False
    # Local installs resolve as local-<slug>; store installs resolve as
    # <repo-hash>-<slug> where the hash is the first 8 hex digits of
    # SHA-1 over the lowercased repository URL (Supervisor store rule:
    # internal name {REPO}_{SLUG} with _ mapped to - for DNS). Locality
    # itself is enforced later by DNS pinning, which rejects every
    # resolved address outside local ranges; these checks are charset
    # hygiene plus exact shape. All-numeric labels are rejected: they
    # read as IP literals, which belong to the address branch above,
    # and must never reach DNS.
    if len(labels) == 1 and host.isdigit():
        return False
    return (
        host == "localhost"
        or (len(labels) == 1 and host.startswith("local-"))
        or (len(labels) == 1 and _is_store_hostname(host))
        or host.endswith(".local")
        or host.endswith(".home.arpa")
    )


def _is_store_hostname(host: str) -> bool:
    """Accept the legacy NLU and unified Stack slugs; DNS must still be local."""
    prefix, separator, slug = host.partition("-")
    return (
        separator == "-"
        and slug in {"ptbr-nlu", "arandu-stack"}
        and len(prefix) == 8
        and all(character in "0123456789abcdef" for character in prefix)
    )


def _origin(host: str, port: int) -> str:
    netloc = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    return urlunsplit(SplitResult("http", netloc, "", "", ""))


def normalize_endpoint(value: str) -> str:
    """Return one canonical local HTTP origin."""

    if type(value) is not str or not value or len(value) > 255:
        raise ClientError("endpoint")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ClientError("endpoint") from error
    if (
        parsed.scheme != "http"
        or parsed.hostname is None
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ClientError("endpoint")
    host = parsed.hostname.lower()
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if not _hostname_is_local(host):
            raise ClientError("endpoint")
    else:
        if not _address_is_local(address):
            raise ClientError("endpoint")
        host = str(address)
    return _origin(host, port)


async def _resolve_host(host: str, port: int) -> tuple[str, ...]:
    result = await asyncio.get_running_loop().getaddrinfo(
        host,
        port,
        type=socket.SOCK_STREAM,
    )
    return tuple(row[4][0] for row in result)


class LocalNluClient:
    """Call only the versioned local add-on endpoints."""

    def __init__(
        self,
        session: Any,
        endpoint: str,
        *,
        resolver: Resolver | None = None,
    ) -> None:
        self._session = session
        self._endpoint = normalize_endpoint(endpoint)
        parsed = urlsplit(self._endpoint)
        if parsed.hostname is None or parsed.port is None:
            raise ClientError("endpoint")
        self._host = parsed.hostname
        self._port = parsed.port
        self._host_header = parsed.netloc
        self._resolver = resolver or _resolve_host
        try:
            self._literal_address = ipaddress.ip_address(self._host)
        except ValueError:
            self._literal_address = None

    async def async_health(self) -> None:
        value = await self._async_json("GET", "/health", None)
        if value != {"status": "ok", "version": 1}:
            raise ClientError("health")

    async def async_interpret(self, payload: dict[str, Any]) -> Any:
        self._last_interpret_protocol = 1
        return await self._async_json("POST", "/v1/interpret", payload)

    async def async_diagnostics(self) -> dict:
        value = await self._async_json("GET", "/diagnostics", None)
        if (type(value) is not dict or set(value) != {"service_version", "protocols", "build_id", "execution"}
                or type(value["service_version"]) is not str or len(value["service_version"]) > 32
                or type(value["protocols"]) is not list or any(type(v) is not int or v not in (1, 2, 3, 4) for v in value["protocols"])
                or type(value["build_id"]) is not str or len(value["build_id"]) > 128 or value["execution"] != "passive"):
            raise ClientError("diagnostics")
        return value

    async def async_resolve(self, payload: dict[str, Any]) -> Any:
        return await self._async_json("POST", "/v2/resolve", payload)

    async def async_interpret_v2(self, payload: dict[str, Any]) -> Any:
        self._last_interpret_protocol = 2
        return await self._async_json("POST", "/v2/interpret", payload)

    async def async_interpret_v3(self, payload: dict[str, Any]) -> Any:
        self._last_interpret_protocol = 3
        return await self._async_json("POST", "/v3/interpret", payload)

    async def async_catalog_v4(self, payload: dict[str, Any]) -> Any:
        return await self._async_json("POST", "/v4/catalog", payload)

    async def async_interpret_v4(self, payload: dict[str, Any]) -> Any:
        self._last_interpret_protocol = 4
        return await self._async_json("POST", "/v4/interpret", payload)

    async def _async_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
    ) -> Any:
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
                origin = await self._async_pinned_origin()
                body = None
                headers = {"Host": self._host_header}
                if payload is not None:
                    body = json.dumps(
                        payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode("utf-8")
                    limit = 2_097_152 if path == "/v4/catalog" else MAX_REQUEST_BYTES
                    if len(body) > limit:
                        raise ClientError("length")
                    headers["Content-Type"] = "application/json"
                request = self._session.request(
                    method,
                    f"{origin}{path}",
                    data=body,
                    headers=headers,
                    allow_redirects=False,
                )
                async with request as response:
                    if response.status != 200:
                        raise ClientError("status")
                    declared = response.headers.get("Content-Length")
                    if declared is not None:
                        try:
                            declared_length = int(declared)
                        except ValueError as error:
                            raise ClientError("length") from error
                        if not 0 <= declared_length <= MAX_RESPONSE_BYTES:
                            raise ClientError("length")
                    body = bytearray()
                    while True:
                        chunk = await response.content.read(8_192)
                        if not chunk:
                            break
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE_BYTES:
                            raise ClientError("length")
        except (ClientError, asyncio.CancelledError):
            raise
        except Exception as error:
            raise ClientError("transport") from error
        try:
            return json.loads(bytes(body))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ClientError("json") from error

    async def _async_pinned_origin(self) -> str:
        if self._literal_address is not None:
            return self._endpoint
        raw_addresses = await self._resolver(self._host, self._port)
        addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
        for raw_address in raw_addresses:
            if type(raw_address) is not str:
                raise ClientError("endpoint")
            try:
                address = ipaddress.ip_address(raw_address)
            except ValueError as error:
                raise ClientError("endpoint") from error
            if not _address_is_local(address):
                raise ClientError("endpoint")
            addresses.add(address)
        if not addresses:
            raise ClientError("endpoint")
        selected = min(
            addresses,
            key=lambda address: (address.version, address.packed),
        )
        return _origin(str(selected), self._port)
