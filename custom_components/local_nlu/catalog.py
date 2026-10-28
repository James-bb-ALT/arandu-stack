"""Build a small deterministic snapshot from exposed Home Assistant entities."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.helpers import area_registry, device_registry, entity_registry

from .const import (
    EFFECT_DOMAINS,
    ER_CATALOG_ID,
    FAN_FEATURE_SET_SPEED,
    FAN_FEATURE_TURN_OFF,
    FAN_FEATURE_TURN_ON,
    MAX_CATALOG_AREAS,
    MAX_CATALOG_ENTITIES,
    MAX_NAME_BYTES,
    MAX_NAMES,
    SUPPORTED_DOMAINS,
)


class CatalogError(Exception):
    """Live Home Assistant state cannot form a safe bounded catalog."""


@dataclass(frozen=True)
class CatalogSnapshot:
    payload: dict[str, Any]


@dataclass(frozen=True)
class ErSnapshot:
    """One canonical entity-resolution snapshot with a content generation."""

    payload: dict[str, Any]


def build_catalog(hass: Any) -> CatalogSnapshot:
    """Return one canonical request-local catalog."""

    from homeassistant.components.homeassistant import exposed_entities

    entities = entity_registry.async_get(hass)
    areas = area_registry.async_get(hass)
    devices = device_registry.async_get(hass)
    rows: list[dict[str, Any]] = []
    used_area_ids: set[str] = set()

    entries = sorted(entities.entities.values(), key=lambda entry: entry.id)
    for entry in entries:
        domain = entry.entity_id.split(".", 1)[0]
        if (
            domain not in SUPPORTED_DOMAINS
            or entry.disabled_by is not None
            or exposed_entities.async_should_expose(
                hass, "conversation", entry.entity_id
            )
            is not True
        ):
            continue
        state = hass.states.get(entry.entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            continue
        names = _entity_names(entry, state)
        if not names:
            continue
        area_id = _effective_area_id(entry, devices)
        if area_id is not None:
            if areas.areas.get(area_id) is None:
                raise CatalogError("area")
            used_area_ids.add(area_id)
        actions = _actions(hass, domain, state)
        if not actions:
            continue
        rows.append(
            {
                "actions": actions,
                "area_id": area_id,
                "domain": domain,
                "entity_id": entry.entity_id,
                "names": names,
                "registry_id": entry.id,
            }
        )

    if len(rows) > MAX_CATALOG_ENTITIES:
        raise CatalogError("entities")
    area_rows: list[dict[str, Any]] = []
    for area_id in sorted(used_area_ids):
        area = areas.areas.get(area_id)
        if area is None:
            raise CatalogError("area")
        names = _bounded_names((area.name, *sorted(area.aliases)))
        if not names:
            raise CatalogError("area_names")
        area_rows.append({"area_id": area_id, "names": names})
    if len(area_rows) > MAX_CATALOG_AREAS:
        raise CatalogError("areas")
    return CatalogSnapshot(
        payload={
            "areas": area_rows,
            "entities": rows,
            "version": 1,
        }
    )


def _actions(hass: Any, domain: str, state: Any) -> list[str]:
    actions = ["get_state"]
    if domain not in EFFECT_DOMAINS:
        return actions
    supported = state.attributes.get("supported_features", 0)
    if (
        hass.services.has_service(domain, "turn_off") is True
        and (
            domain != "fan"
            or (
                type(supported) is int
                and bool(supported & FAN_FEATURE_TURN_OFF)
            )
        )
    ):
        actions.append("turn_off")
    if (
        hass.services.has_service(domain, "turn_on") is True
        and (
            domain != "fan"
            or (
                type(supported) is int
                and bool(supported & FAN_FEATURE_TURN_ON)
            )
        )
    ):
        actions.append("turn_on")
    if (
        domain == "fan"
        and hass.services.has_service("fan", "set_percentage") is True
        and type(supported) is int
        and bool(supported & FAN_FEATURE_SET_SPEED)
    ):
        actions.append("set_fan_percentage")
    actions.sort()
    return actions


def _effective_area_id(entry: Any, devices: Any) -> str | None:
    if type(entry.area_id) is str:
        return entry.area_id
    if type(entry.device_id) is not str:
        return None
    device = devices.async_get(entry.device_id)
    return device.area_id if device is not None and type(device.area_id) is str else None


def _entity_names(entry: Any, state: Any) -> list[str]:
    aliases = entry.aliases if type(entry.aliases) is list else []
    return _bounded_names(
        (
            state.attributes.get("friendly_name"),
            getattr(entry, "name", None),
            getattr(entry, "original_name_unprefixed", None),
            getattr(entry, "original_name", None),
            *aliases,
        )
    )


def _bounded_names(values: tuple[Any, ...]) -> list[str]:
    names = {
        value.strip()
        for value in values
        if type(value) is str
        and value.strip()
        and len(value.strip().encode("utf-8")) <= MAX_NAME_BYTES
        and not any(
            ord(character) < 32 or ord(character) == 127
            for character in value.strip()
        )
    }
    return sorted(names)[:MAX_NAMES]


def _display_name(entry: Any, state: Any) -> str | None:
    """Return the single normative display name for entity resolution."""
    candidates = (
        state.attributes.get("friendly_name"),
        getattr(entry, "name", None),
        getattr(entry, "original_name_unprefixed", None),
        getattr(entry, "original_name", None),
    )
    for candidate in candidates:
        if (
            type(candidate) is str
            and candidate.strip()
            and len(candidate.strip().encode("utf-8")) <= MAX_NAME_BYTES
            and not any(
                ord(character) < 32 or ord(character) == 127
                for character in candidate.strip()
            )
        ):
            return candidate.strip()
    return None


def _er_generation(descriptors: dict[str, Any]) -> str:
    """Return the SHA-256 generation over the descriptor set only."""
    canonical = json.dumps(
        descriptors,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def build_er_snapshot(hass: Any) -> ErSnapshot:
    """Return one canonical entity-resolution snapshot (additive; v1 untouched)."""
    from homeassistant.components.homeassistant import exposed_entities

    entities = entity_registry.async_get(hass)
    areas = area_registry.async_get(hass)
    devices = device_registry.async_get(hass)
    rows: list[dict[str, Any]] = []
    used_area_ids: set[str] = set()

    entries = sorted(entities.entities.values(), key=lambda entry: entry.id)
    for entry in entries:
        domain = entry.entity_id.split(".", 1)[0]
        if (
            domain not in SUPPORTED_DOMAINS
            or entry.disabled_by is not None
            or exposed_entities.async_should_expose(
                hass, "conversation", entry.entity_id
            )
            is not True
        ):
            continue
        state = hass.states.get(entry.entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            continue
        display_name = _display_name(entry, state)
        if display_name is None:
            continue
        aliases = entry.aliases if type(entry.aliases) is list else []
        area_id = _effective_area_id(entry, devices)
        if area_id is not None:
            if areas.areas.get(area_id) is None:
                raise CatalogError("area")
            used_area_ids.add(area_id)
        capabilities = _actions(hass, domain, state)
        if not capabilities:
            continue
        rows.append(
            {
                "aliases": _bounded_names(tuple(aliases)),
                "area_id": area_id,
                "capabilities": capabilities,
                "display_name": display_name,
                "domain": domain,
                "entity_id": entry.entity_id,
                "registry_id": entry.id,
            }
        )

    if len(rows) > MAX_CATALOG_ENTITIES:
        raise CatalogError("entities")
    area_rows: list[dict[str, Any]] = []
    for area_id in sorted(used_area_ids):
        area = areas.areas.get(area_id)
        if area is None:
            raise CatalogError("area")
        names = _bounded_names((area.name, *sorted(area.aliases)))
        if not names:
            raise CatalogError("area_names")
        area_rows.append({"area_id": area_id, "names": names})
    if len(area_rows) > MAX_CATALOG_AREAS:
        raise CatalogError("areas")
    descriptors = {
        "areas": area_rows,
        "catalog_id": ER_CATALOG_ID,
        "entities": sorted(rows, key=lambda row: row["registry_id"]),
    }
    return ErSnapshot(
        payload={**descriptors, "generation": _er_generation(descriptors)}
    )
