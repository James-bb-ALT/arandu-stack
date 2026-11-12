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
    failed_targets: tuple[str, ...] = ()
    total_targets: int = 0
    delivery_status: str = "unknown"  # "accepted_by_ha" | "playback_observed" | "partial" | "unknown" | "failed"
    reason: str | None = None


def is_entity_exposed(hass: HomeAssistant, entity_id: str) -> bool:
    """Check whether entity is exposed to Assist conversation. Fails closed."""
    try:
        from homeassistant.components.homeassistant import exposed_entities
        return exposed_entities.async_should_expose(hass, "conversation", entity_id) is True
    except Exception:
        return False


def check_user_permission(user: Any, entity_id: str) -> bool:
    """Validate active user has explicit read and control permissions on entity. Fails closed."""
    if user is None or getattr(user, "is_active", True) is not True:
        return False
    permissions = getattr(user, "permissions", None)
    if permissions is None:
        return False
    try:
        can_read = permissions.check_entity(entity_id, POLICY_READ)
        can_control = permissions.check_entity(entity_id, POLICY_CONTROL)
        return can_read is True and can_control is True
    except Exception:
        return False


def get_dest_config_hash(options: AnnouncementOptions) -> str:
    """Compute a hash of configured destinations and policy to detect configuration shifts."""
    items = []
    for area_key, entities in sorted(options.destinations.items()):
        ent_str = ",".join(f"{e.kind}:{e.entity_id}" for e in entities)
        items.append(f"{area_key}->{ent_str}")
    quiet_str = (
        f"{options.quiet_hours.get('start', '')}-{options.quiet_hours.get('end', '')}"
        if options.quiet_hours
        else ""
    )
    policy = (
        f"en:{options.enabled};all:{options.allow_all};conf:{options.require_confirm_all};"
        f"tts:{options.tts_entity};chime:{options.chime};max:{options.max_targets};"
        f"quiet:{quiet_str}"
    )
    content = ";".join(items) + ";" + policy
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def resolve_targets(
    hass: HomeAssistant,
    destination: str | None,
    options: AnnouncementOptions,
    origin_satellite: str | None = None,
    kind: str = "area",
) -> tuple[tuple[DestinationEntity, ...], str | None]:
    """Resolve destination string to approved DestinationEntity objects without substring guessing.

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
            return (), "no_destination"

        if len(all_targets) > options.max_targets:
            return (), f"too_many_targets:{len(all_targets)}"

        return tuple(all_targets), None

    if not destination:
        return (), "no_destination"

    norm_dest = normalize_syntax(destination)

    # 1. Exact configured entity_id match
    exact_entity_matches: list[DestinationEntity] = []
    seen_eids: set[str] = set()
    for entities in options.destinations.values():
        for ent in entities:
            if normalize_syntax(ent.entity_id) == norm_dest and ent.entity_id not in seen_eids:
                seen_eids.add(ent.entity_id)
                exact_entity_matches.append(ent)
    if len(exact_entity_matches) == 1:
        return (exact_entity_matches[0],), None
    if len(exact_entity_matches) > 1:
        return (), "ambiguous_destination"

    # 2. Exact key match in options.destinations (normalized)
    exact_key_matches: list[tuple[str, tuple[DestinationEntity, ...]]] = []
    for key, entities in options.destinations.items():
        if normalize_syntax(key) == norm_dest:
            exact_key_matches.append((key, entities))
    if len(exact_key_matches) == 1:
        return _select_single_destination(exact_key_matches[0][1], norm_dest)
    if len(exact_key_matches) > 1:
        return (), "ambiguous_destination"

    # 3. Exact Home Assistant area registry match (area name or area id)
    try:
        areas = area_registry.async_get(hass)
        area_matches: list[tuple[DestinationEntity, ...]] = []
        for area in areas.async_list_areas():
            area_name_norm = normalize_syntax(area.name)
            area_id_norm = normalize_syntax(area.id)
            if norm_dest in (area_name_norm, area_id_norm):
                for key in (area.id, area.name):
                    norm_k = normalize_syntax(key)
                    for opt_k, opt_ents in options.destinations.items():
                        if normalize_syntax(opt_k) == norm_k:
                            area_matches.append(opt_ents)
                            break
                    else:
                        continue
                    break
        if len(area_matches) == 1:
            return _select_single_destination(area_matches[0], norm_dest)
        if len(area_matches) > 1:
            return (), "ambiguous_destination"
    except Exception:
        pass

    # 4. Exact unique alias or friendly name match among configured destination entities
    alias_matches: list[DestinationEntity] = []
    seen_alias_eids: set[str] = set()
    try:
        ent_reg = entity_registry.async_get(hass)
    except Exception:
        ent_reg = None

    for entities in options.destinations.values():
        for ent in entities:
            if ent.entity_id in seen_alias_eids:
                continue
            matched = False
            if ent_reg is not None:
                entry = ent_reg.entities.get(ent.entity_id)
                if entry is not None:
                    aliases = getattr(entry, "aliases", None) or []
                    if any(normalize_syntax(a) == norm_dest for a in aliases if isinstance(a, str)):
                        matched = True
                    name = getattr(entry, "name", None) or getattr(entry, "original_name", None)
                    if isinstance(name, str) and normalize_syntax(name) == norm_dest:
                        matched = True
            state = hass.states.get(ent.entity_id)
            if state is not None:
                friendly = getattr(state, "attributes", {}).get("friendly_name")
                if isinstance(friendly, str) and normalize_syntax(friendly) == norm_dest:
                    matched = True
            if matched:
                seen_alias_eids.add(ent.entity_id)
                alias_matches.append(ent)

    if len(alias_matches) == 1:
        return (alias_matches[0],), None
    if len(alias_matches) > 1:
        return (), "ambiguous_destination"

    return (), "no_destination"


def _select_single_destination(
    candidates: tuple[DestinationEntity, ...],
    norm_dest: str,
) -> tuple[tuple[DestinationEntity, ...], str | None]:
    """Select the single appropriate entity without arbitrary guessing."""
    if not candidates:
        return (), "no_destination"

    if len(candidates) == 1:
        return (candidates[0],), None

    # Multiple candidates in same area: require explicit disambiguation keyword
    if any(kw in norm_dest for kw in ("alto-falante", "alto falante", "speaker", "caixa")):
        mps = [c for c in candidates if c.kind == "media_player"]
        if len(mps) == 1:
            return (mps[0],), None
        if len(mps) > 1:
            return (), "ambiguous_destination"

    if any(kw in norm_dest for kw in ("satelite", "satélite", "assistente")):
        sats = [c for c in candidates if c.kind == "assist_satellite"]
        if len(sats) == 1:
            return (sats[0],), None
        if len(sats) > 1:
            return (), "ambiguous_destination"

    return (), "ambiguous_destination"


def _find_linked_media_player(
    options: AnnouncementOptions,
    satellite_entity_id: str,
) -> DestinationEntity | None:
    """Find an explicitly configured media_player paired in the same area as the satellite."""
    linked: list[DestinationEntity] = []
    for entities in options.destinations.values():
        if any(e.entity_id == satellite_entity_id for e in entities):
            for e in entities:
                if e.kind == "media_player" and e.entity_id.startswith("media_player."):
                    linked.append(e)
    if len(linked) == 1:
        return linked[0]
    return None


def _validate_target_ready(
    hass: HomeAssistant,
    target: DestinationEntity,
    options: AnnouncementOptions,
    user: Any,
    ent_reg: Any,
) -> tuple[DestinationEntity | None, str | None, str | None]:
    """Validate target entity exposure, permission, state, disabled_by, and service.

    Returns (effective_target, error_code, reason).
    """
    entity_id = target.entity_id

    if ent_reg is not None:
        entry = ent_reg.entities.get(entity_id)
        if entry and getattr(entry, "disabled_by", None) is not None:
            return None, "destination_unavailable", entity_id

    state = hass.states.get(entity_id)
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return None, "destination_unavailable", entity_id

    if not is_entity_exposed(hass, entity_id):
        return None, "permission_denied", "not_exposed"

    if not check_user_permission(user, entity_id):
        return None, "permission_denied", "user_permission"

    if target.kind == "assist_satellite":
        if hass.services.has_service("assist_satellite", "announce"):
            return target, None, None
        # Fallback ONLY if an explicit media_player is linked to this destination
        linked_mp = _find_linked_media_player(options, entity_id)
        if linked_mp is None:
            return None, "announcement_fallback_unavailable", entity_id
        # Validate the linked media_player itself
        return _validate_target_ready(hass, linked_mp, options, user, ent_reg)

    if target.kind == "media_player":
        if not entity_id.startswith("media_player."):
            return None, "announcement_fallback_unavailable", entity_id
        if not options.tts_entity or not options.tts_entity.startswith("tts."):
            return None, "no_tts_entity", entity_id
        if not hass.services.has_service("tts", "speak"):
            return None, "tts_service_unavailable", entity_id
        return target, None, None

    return None, "no_destination", entity_id


async def deliver_announcement(
    hass: HomeAssistant,
    targets: tuple[DestinationEntity, ...],
    message: str,
    options: AnnouncementOptions,
    user: Any,
    context: Context | None = None,
) -> DeliveryResult:
    """Deliver announcement to target entities with pre-flight and per-target revalidation."""
    if not targets:
        return DeliveryResult(success=False, error_code="no_destination", delivery_status="failed")

    ent_reg = None
    try:
        ent_reg = entity_registry.async_get(hass)
    except Exception:
        pass

    # 1. Pre-flight validation on all targets before any side effect
    for target in targets:
        _, err_code, reason = _validate_target_ready(hass, target, options, user, ent_reg)
        if err_code is not None:
            return DeliveryResult(
                success=False,
                error_code=err_code,
                total_targets=len(targets),
                delivery_status="failed",
                reason=reason,
            )

    # 2. Execution loop bounded by total budget, revalidating immediately before each async_call
    delivered: list[str] = []

    async def _send_to_target(target: DestinationEntity) -> str:
        eff_target, err_code, reason = _validate_target_ready(hass, target, options, user, ent_reg)
        if err_code is not None or eff_target is None:
            raise _TargetValidationError(err_code or "announce_failed", reason or target.entity_id)

        entity_id = eff_target.entity_id

        if eff_target.kind == "assist_satellite":
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
            return entity_id

        if eff_target.kind == "media_player":
            assert options.tts_entity is not None
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
            return entity_id

        raise _TargetValidationError("no_destination", target.entity_id)

    try:
        async with asyncio.timeout(TIMEOUT_TOTAL_BUDGET):
            for target in targets:
                try:
                    accepted_id = await _send_to_target(target)
                    delivered.append(accepted_id)
                except _TargetValidationError as ve:
                    return DeliveryResult(
                        success=False,
                        error_code=ve.error_code,
                        delivered_targets=tuple(delivered),
                        failed_targets=(target.entity_id,),
                        total_targets=len(targets),
                        delivery_status="partial" if delivered else "failed",
                        reason=ve.reason,
                    )
                except (asyncio.TimeoutError, Exception) as exc:
                    _LOGGER.warning("Failed delivery to %s: %s", target.entity_id, exc)
                    return DeliveryResult(
                        success=False,
                        error_code="announce_failed",
                        delivered_targets=tuple(delivered),
                        failed_targets=(target.entity_id,),
                        total_targets=len(targets),
                        delivery_status="partial" if delivered else "failed",
                        reason=target.entity_id,
                    )

    except asyncio.TimeoutError:
        return DeliveryResult(
            success=False,
            error_code="announce_failed",
            delivered_targets=tuple(delivered),
            total_targets=len(targets),
            delivery_status="partial" if delivered else "failed",
            reason="budget_timeout",
        )

    return DeliveryResult(
        success=True,
        delivered_targets=tuple(delivered),
        total_targets=len(targets),
        delivery_status="accepted_by_ha",
    )


class _TargetValidationError(Exception):
    def __init__(self, error_code: str, reason: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code
        self.reason = reason
