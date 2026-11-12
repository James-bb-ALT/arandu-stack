"""Delivery engine for deterministic announcements.

Priority 1: assist_satellite.announce
Priority 2: tts.speak (media_player fallback)
Zero audio streaming or remote mic capture.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import logging
from typing import Any

from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import area_registry, entity_registry

from .announcement_options import AnnouncementOptions, DestinationEntity
from .announcement_parser import normalize_syntax

_LOGGER = logging.getLogger(__name__)

TIMEOUT_PER_TARGET = 15.0
TIMEOUT_TOTAL_BUDGET = 30.0


@dataclass(frozen=True)
class DeliveryResult:
    success: bool
    error_code: str | None = None
    delivered_targets: tuple[str, ...] = ()
    reason: str | None = None


def is_entity_exposed(hass: HomeAssistant, entity_id: str) -> bool:
    """Check whether entity is exposed to Assist conversation."""
    try:
        from homeassistant.components.homeassistant import exposed_entities
        return exposed_entities.async_should_expose(hass, "conversation", entity_id) is True
    except Exception:
        # Fallback for minimal test environments
        exposed_attr = getattr(hass, "exposed", None)
        if exposed_attr is not None:
            return entity_id in exposed_attr
        return True


def check_user_permission(user: Any, entity_id: str) -> bool:
    """Validate user has read and control permissions on entity."""
    if user is None:
        return False
    # If user has no permissions object (e.g. system/unrestricted), allow
    permissions = getattr(user, "permissions", None)
    if permissions is None:
        return True
    try:
        can_read = permissions.check_entity(entity_id, POLICY_READ)
        can_control = permissions.check_entity(entity_id, POLICY_CONTROL)
        return bool(can_read and can_control)
    except Exception:
        return False


def get_dest_config_hash(options: AnnouncementOptions) -> str:
    """Compute a hash of configured destinations to detect configuration shifts."""
    items = []
    for area_key, entities in sorted(options.destinations.items()):
        ent_str = ",".join(f"{e.kind}:{e.entity_id}" for e in entities)
        items.append(f"{area_key}->{ent_str}")
    content = ";".join(items) + f";tts:{options.tts_entity}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def resolve_targets(
    hass: HomeAssistant,
    destination: str | None,
    options: AnnouncementOptions,
    origin_satellite: str | None = None,
    kind: str = "area",
) -> tuple[tuple[DestinationEntity, ...], str | None]:
    """Resolve destination string to approved DestinationEntity objects.

    Returns (entities, error_code).
    """
    if not options.destinations:
        return (), "no_destination"

    if kind == "all" or destination in ("all", "casa toda", "geral"):
        all_targets: list[DestinationEntity] = []
        seen: set[str] = set()

        for entities in options.destinations.values():
            for ent in entities:
                if ent.entity_id not in seen:
                    # Avoid self-echo in broadcast
                    if origin_satellite and ent.entity_id == origin_satellite:
                        continue
                    seen.add(ent.entity_id)
                    all_targets.append(ent)

        if not all_targets:
            # If all targets were filtered out or empty
            return (), "no_destination"

        # Bound by max_targets
        return tuple(all_targets[: options.max_targets]), None

    if not destination:
        return (), "no_destination"

    norm_dest = normalize_syntax(destination)

    # 1. Match destination directly against keys in options.destinations
    if norm_dest in options.destinations:
        candidates = options.destinations[norm_dest]
        return _select_single_destination(candidates, norm_dest)

    # 2. Match via Home Assistant area registry
    try:
        areas = area_registry.async_get(hass)
        for area in areas.async_list_areas():
            area_name_norm = normalize_syntax(area.name)
            area_id_norm = normalize_syntax(area.id)
            if norm_dest in (area_name_norm, area_id_norm):
                # Look up by area_id or area_name in destinations
                for key in (area_id_norm, area_name_norm, area.id, area.name):
                    norm_k = normalize_syntax(key)
                    if norm_k in options.destinations:
                        candidates = options.destinations[norm_k]
                        return _select_single_destination(candidates, norm_dest)
    except Exception:
        pass

    # 3. Match substring or entity friendly_name / entity_id
    for key, entities in options.destinations.items():
        if key in norm_dest or norm_dest in key:
            return _select_single_destination(entities, norm_dest)
        for ent in entities:
            if ent.entity_id == norm_dest or ent.entity_id in norm_dest:
                return (ent,), None

    return (), "no_destination"


def _select_single_destination(
    candidates: tuple[DestinationEntity, ...],
    norm_dest: str,
) -> tuple[tuple[DestinationEntity, ...], str | None]:
    """Select the single appropriate entity without silent amplification."""
    if not candidates:
        return (), "no_destination"

    if len(candidates) == 1:
        return (candidates[0],), None

    # If multiple candidates in area:
    # Check if user mentioned satellite or speaker specifically
    if any(kw in norm_dest for kw in ("alto-falante", "alto falante", "speaker", "caixa")):
        for c in candidates:
            if c.kind == "media_player":
                return (c,), None
    if any(kw in norm_dest for kw in ("satelite", "satélite", "assistente")):
        for c in candidates:
            if c.kind == "assist_satellite":
                return (c,), None

    # Priority 1: assist_satellite
    for c in candidates:
        if c.kind == "assist_satellite":
            return (c,), None

    # Fallback to the first
    return (candidates[0],), None


async def deliver_announcement(
    hass: HomeAssistant,
    targets: tuple[DestinationEntity, ...],
    message: str,
    options: AnnouncementOptions,
    user: Any,
    context: Context | None = None,
) -> DeliveryResult:
    """Deliver announcement to target entities with timeouts and safety checks."""
    if not targets:
        return DeliveryResult(success=False, error_code="no_destination")

    # 1. Pre-flight validation on all targets
    ent_reg = None
    try:
        ent_reg = entity_registry.async_get(hass)
    except Exception:
        pass

    for target in targets:
        entity_id = target.entity_id

        # Disabled check
        if ent_reg is not None:
            entry = ent_reg.entities.get(entity_id)
            if entry and getattr(entry, "disabled_by", None) is not None:
                return DeliveryResult(success=False, error_code="destination_unavailable", reason=entity_id)

        # Available state check
        state = hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return DeliveryResult(success=False, error_code="destination_unavailable", reason=entity_id)

        # Exposure check
        if not is_entity_exposed(hass, entity_id):
            return DeliveryResult(success=False, error_code="permission_denied", reason="not_exposed")

        # Permission check
        if not check_user_permission(user, entity_id):
            return DeliveryResult(success=False, error_code="permission_denied", reason="user_permission")

    # 2. Execution loop bounded by total budget
    delivered: list[str] = []

    async def _send_to_target(target: DestinationEntity) -> bool:
        entity_id = target.entity_id

        # Priority 1: assist_satellite.announce
        if target.kind == "assist_satellite" and hass.services.has_service("assist_satellite", "announce"):
            await asyncio.wait_for(
                hass.services.async_call(
                    "assist_satellite",
                    "announce",
                    {"message": message, "preannounce": options.chime},
                    blocking=True,
                    context=context,
                    target={"entity_id": [entity_id]},
                ),
                timeout=TIMEOUT_PER_TARGET,
            )
            return True

        # Priority 2: tts.speak
        if target.kind == "media_player" or (
            target.kind == "assist_satellite" and not hass.services.has_service("assist_satellite", "announce")
        ):
            if not options.tts_entity:
                _LOGGER.warning("TTS entity not configured for media_player announcement to %s", entity_id)
                raise ValueError("no_tts_entity")

            if not hass.services.has_service("tts", "speak"):
                _LOGGER.warning("Service tts.speak not available in Home Assistant")
                raise ValueError("tts_service_unavailable")

            await asyncio.wait_for(
                hass.services.async_call(
                    "tts",
                    "speak",
                    {
                        "media_player_entity_id": entity_id,
                        "message": message,
                        "cache": False,
                    },
                    blocking=True,
                    context=context,
                    target={"entity_id": [options.tts_entity]},
                ),
                timeout=TIMEOUT_PER_TARGET,
            )
            return True

        return False

    try:
        async with asyncio.timeout(TIMEOUT_TOTAL_BUDGET):
            for target in targets:
                try:
                    ok = await _send_to_target(target)
                    if ok:
                        delivered.append(target.entity_id)
                except ValueError as ve:
                    return DeliveryResult(
                        success=False,
                        error_code=str(ve),
                        delivered_targets=tuple(delivered),
                        reason=target.entity_id,
                    )
                except (asyncio.TimeoutError, Exception) as exc:
                    _LOGGER.warning("Failed delivery to %s: %s", target.entity_id, exc)
                    return DeliveryResult(
                        success=False,
                        error_code="announce_failed",
                        delivered_targets=tuple(delivered),
                        reason=target.entity_id,
                    )

    except asyncio.TimeoutError:
        return DeliveryResult(
            success=False,
            error_code="announce_failed",
            delivered_targets=tuple(delivered),
            reason="budget_timeout",
        )

    return DeliveryResult(
        success=True,
        delivered_targets=tuple(delivered),
    )
