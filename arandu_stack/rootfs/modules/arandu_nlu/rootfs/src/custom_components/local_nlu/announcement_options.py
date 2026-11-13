"""Configuration options and validation for deterministic announcements."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
import logging
import re
from typing import Any, Literal

_LOGGER = logging.getLogger(__name__)

CONF_ANNOUNCEMENTS_ENABLED = "announcements_enabled"
CONF_ANNOUNCEMENTS_ALLOW_ALL = "announcements_allow_all"
CONF_ANNOUNCEMENTS_REQUIRE_CONFIRM_ALL = "announcements_require_confirm_all"
CONF_ANNOUNCEMENTS_DESTINATIONS = "announcements_destinations"
CONF_ANNOUNCEMENTS_TTS_ENTITY = "announcements_tts_entity"
CONF_ANNOUNCEMENTS_CHIME = "announcements_chime"
CONF_ANNOUNCEMENTS_MAX_TARGETS = "announcements_max_targets"
CONF_ANNOUNCEMENTS_COOLDOWN_S = "announcements_cooldown_s"
CONF_ANNOUNCEMENTS_SESSION_TTL_S = "announcements_session_ttl_s"
CONF_ANNOUNCEMENTS_QUIET_HOURS = "announcements_quiet_hours"

DEFAULT_ENABLED = False
DEFAULT_ALLOW_ALL = False
DEFAULT_REQUIRE_CONFIRM_ALL = True
DEFAULT_CHIME = True
DEFAULT_MAX_TARGETS = 6
DEFAULT_COOLDOWN_S = 5
DEFAULT_SESSION_TTL_S = 30

MIN_MAX_TARGETS, MAX_MAX_TARGETS = 1, 8
MIN_COOLDOWN_S, MAX_COOLDOWN_S = 3, 60
MIN_SESSION_TTL_S, MAX_SESSION_TTL_S = 10, 60

MAX_AREAS = 32
MAX_ENTITIES_PER_AREA = 2

TargetKind = Literal["assist_satellite", "media_player"]


@dataclass(frozen=True)
class DestinationEntity:
    kind: TargetKind
    entity_id: str


@dataclass(frozen=True)
class AnnouncementOptions:
    enabled: bool = DEFAULT_ENABLED
    allow_all: bool = DEFAULT_ALLOW_ALL
    require_confirm_all: bool = DEFAULT_REQUIRE_CONFIRM_ALL
    destinations: dict[str, tuple[DestinationEntity, ...]] = field(default_factory=dict)
    tts_entity: str | None = None
    chime: bool = DEFAULT_CHIME
    max_targets: int = DEFAULT_MAX_TARGETS
    cooldown_s: int = DEFAULT_COOLDOWN_S
    session_ttl_s: int = DEFAULT_SESSION_TTL_S
    quiet_hours: dict[str, str] | None = None


def _parse_time(t_str: str) -> time | None:
    if not isinstance(t_str, str):
        return None
    match = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", t_str.strip())
    if not match:
        return None
    return time(hour=int(match.group(1)), minute=int(match.group(2)))


def is_in_quiet_hours(
    options: AnnouncementOptions,
    current_time: time | None = None,
    hass: Any | None = None,
) -> bool:
    """Check if the given time falls within configured quiet hours using HA timezone."""
    if not options.quiet_hours:
        return False
    start_str = options.quiet_hours.get("start")
    end_str = options.quiet_hours.get("end")
    if not start_str or not end_str:
        return False

    start = _parse_time(start_str)
    end = _parse_time(end_str)
    if not start or not end:
        return False

    if current_time is None:
        current_time = _get_ha_local_time(hass)

    if start <= end:
        return start <= current_time <= end
    # Overnight range, e.g. 22:00 to 07:00
    return current_time >= start or current_time <= end


def _get_ha_local_time(hass: Any | None = None) -> time:
    """Resolve current local time in Home Assistant's configured timezone."""
    try:
        from homeassistant.util import dt as dt_util

        tz_name = getattr(getattr(hass, "config", None), "time_zone", None) if hass is not None else None
        if tz_name and hasattr(dt_util, "get_time_zone") and hasattr(dt_util, "utcnow"):
            tz = dt_util.get_time_zone(tz_name)
            if tz is not None:
                return dt_util.utcnow().astimezone(tz).time()
        if hasattr(dt_util, "now"):
            return dt_util.now().time()
        if hasattr(dt_util, "as_local") and hasattr(dt_util, "utcnow"):
            return dt_util.as_local(dt_util.utcnow()).time()
    except Exception:
        pass

    tz_name = getattr(getattr(hass, "config", None), "time_zone", None) if hass is not None else None
    if tz_name:
        try:
            from zoneinfo import ZoneInfo

            return datetime.now(ZoneInfo(tz_name)).time()
        except Exception:
            pass
    return datetime.now().astimezone().time()


def load_announcement_options(raw: dict[str, Any]) -> AnnouncementOptions:
    """Parse and bound options safely. Preserves older or unrelated keys."""
    enabled = bool(raw.get(CONF_ANNOUNCEMENTS_ENABLED, DEFAULT_ENABLED))
    allow_all = bool(raw.get(CONF_ANNOUNCEMENTS_ALLOW_ALL, DEFAULT_ALLOW_ALL))
    require_confirm_all = True  # Hard requirement for this phase

    # Parse and validate destinations
    raw_destinations = raw.get(CONF_ANNOUNCEMENTS_DESTINATIONS, {})
    clean_destinations: dict[str, tuple[DestinationEntity, ...]] = {}

    if isinstance(raw_destinations, dict):
        for area_key, entities in list(raw_destinations.items())[:MAX_AREAS]:
            if not isinstance(area_key, str) or not area_key.strip():
                continue
            norm_area_key = area_key.strip().lower()

            entity_list: list[DestinationEntity] = []
            if isinstance(entities, list):
                for item in entities[:MAX_ENTITIES_PER_AREA]:
                    if isinstance(item, dict):
                        kind = item.get("kind")
                        entity_id = item.get("entity_id")
                        if kind in ("assist_satellite", "media_player") and isinstance(entity_id, str):
                            entity_list.append(DestinationEntity(kind=kind, entity_id=entity_id.strip()))
            elif isinstance(entities, dict):
                kind = entities.get("kind")
                entity_id = entities.get("entity_id")
                if kind in ("assist_satellite", "media_player") and isinstance(entity_id, str):
                    entity_list.append(DestinationEntity(kind=kind, entity_id=entity_id.strip()))

            if entity_list:
                clean_destinations[norm_area_key] = tuple(entity_list)

    tts_entity = raw.get(CONF_ANNOUNCEMENTS_TTS_ENTITY)
    if not isinstance(tts_entity, str) or not tts_entity.startswith("tts."):
        tts_entity = None
    else:
        tts_entity = tts_entity.strip()

    chime = bool(raw.get(CONF_ANNOUNCEMENTS_CHIME, DEFAULT_CHIME))

    try:
        max_targets = int(raw.get(CONF_ANNOUNCEMENTS_MAX_TARGETS, DEFAULT_MAX_TARGETS))
        max_targets = max(MIN_MAX_TARGETS, min(MAX_MAX_TARGETS, max_targets))
    except (TypeError, ValueError):
        max_targets = DEFAULT_MAX_TARGETS

    try:
        cooldown_s = int(raw.get(CONF_ANNOUNCEMENTS_COOLDOWN_S, DEFAULT_COOLDOWN_S))
        cooldown_s = max(MIN_COOLDOWN_S, min(MAX_COOLDOWN_S, cooldown_s))
    except (TypeError, ValueError):
        cooldown_s = DEFAULT_COOLDOWN_S

    try:
        session_ttl_s = int(raw.get(CONF_ANNOUNCEMENTS_SESSION_TTL_S, DEFAULT_SESSION_TTL_S))
        session_ttl_s = max(MIN_SESSION_TTL_S, min(MAX_SESSION_TTL_S, session_ttl_s))
    except (TypeError, ValueError):
        session_ttl_s = DEFAULT_SESSION_TTL_S

    raw_quiet = raw.get(CONF_ANNOUNCEMENTS_QUIET_HOURS)
    clean_quiet: dict[str, str] | None = None
    if isinstance(raw_quiet, dict):
        start = _parse_time(raw_quiet.get("start", ""))
        end = _parse_time(raw_quiet.get("end", ""))
        if start and end:
            clean_quiet = {
                "start": start.strftime("%H:%M"),
                "end": end.strftime("%H:%M"),
            }

    return AnnouncementOptions(
        enabled=enabled,
        allow_all=allow_all,
        require_confirm_all=require_confirm_all,
        destinations=clean_destinations,
        tts_entity=tts_entity,
        chime=chime,
        max_targets=max_targets,
        cooldown_s=cooldown_s,
        session_ttl_s=session_ttl_s,
        quiet_hours=clean_quiet,
    )
