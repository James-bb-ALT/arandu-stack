#!/usr/bin/env python3
"""Supervisor discovery for Home Assistant Wyoming integration."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LOG = logging.getLogger("arandu.discovery")
SUPERVISOR = "http://supervisor"
SERVICE = "wyoming"


@dataclass(frozen=True)
class DiscoveryResult:
    uuid: str | None
    uri: str
    changed: bool


class DiscoveryError(RuntimeError):
    """Supervisor discovery failed."""


def _api_json(method: str, path: str, token: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = Request(
        f"{SUPERVISOR}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urlopen(req, timeout=10) as res:
        body = res.read()
    if not body:
        return {}
    parsed = json.loads(body.decode("utf-8"))
    if isinstance(parsed, dict) and parsed.get("result") == "ok" and isinstance(parsed.get("data"), dict):
        return parsed["data"]
    return parsed


def _normalize_hostname(value: str) -> str:
    return value.replace("_", "-")


def _hostname_from_self_info(token: str) -> str | None:
    try:
        info = _api_json("GET", "/addons/self/info", token)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        LOG.warning("[DISCOVERY] Falha lendo /addons/self/info: %s", exc)
        return None
    hostname = info.get("hostname")
    if isinstance(hostname, str) and hostname:
        return _normalize_hostname(hostname)
    slug = info.get("slug")
    repository = info.get("repository")
    if isinstance(slug, str) and slug:
        if isinstance(repository, str) and repository and repository not in ("unknown", "null"):
            return _normalize_hostname(f"{repository}_{slug}")
        return _normalize_hostname(slug)
    return None


def discovery_uri(port: int = 10350, token: str | None = None) -> str:
    token = token or os.environ.get("SUPERVISOR_TOKEN")
    host = _hostname_from_self_info(token) if token else None
    if not host:
        host = _normalize_hostname(os.environ.get("HOSTNAME", "arandu_stt"))
    return f"tcp://{host}:{port}"


def publish_wyoming_discovery(port: int = 10350) -> DiscoveryResult | None:
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        LOG.warning("[DISCOVERY] SUPERVISOR_TOKEN ausente; descoberta automática desativada")
        return None

    uri = discovery_uri(port=port, token=token)
    payload = {"service": SERVICE, "config": {"uri": uri}}
    try:
        data = _api_json("POST", "/discovery", token, payload)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise DiscoveryError(f"falha publicando discovery Wyoming: {exc}") from exc

    uuid = data.get("uuid") if isinstance(data, dict) else None
    LOG.info("[DISCOVERY] Wyoming publicado uri=%s uuid=%s", uri, uuid or "?")
    return DiscoveryResult(uuid=uuid, uri=uri, changed=True)
