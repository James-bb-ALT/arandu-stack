"""Consume authorized HA area observations; never estimate BLE position.

Provider timestamps are mandatory. HA last_changed is never freshness evidence.
"""
from __future__ import annotations

from datetime import datetime, timezone

from homeassistant.helpers import area_registry

from .contextual_protocol import normalize
from .queries import QueryError, number, now


def observation_age(hass, value, max_age=300) -> float:
    try:
        if type(value) in (int, float):
            stamp = datetime.fromtimestamp(number(value), timezone.utc)
        elif type(value) is str:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError
        else:
            raise ValueError
        age = (now(hass) - stamp).total_seconds()
        if not 0 <= age <= max_age:
            raise ValueError
    except (TypeError, ValueError, OverflowError, OSError, QueryError):
        raise QueryError("location_stale") from None
    return age


def read_location(hass, row, state, snapshot, user, options, live) -> dict:
    did = row.get("attributes", {}).get("reference_device_id") or row.get("device_id")
    binding = options.get("device_sensor_bindings", {}).get(did, {})
    preference = options.get("entity_preferences", {}).get(row["entity_id"], {})
    source = row
    if not row.get("attributes", {}).get("dynamic_location"):
        candidates = [candidate for candidate in snapshot.by_id.values() if candidate.get("attributes", {}).get("dynamic_location") and
                      (candidate.get("attributes", {}).get("reference_device_id") or candidate.get("device_id")) == did and did is not None]
        if not candidates:
            raise QueryError("no_location_provider")
        if len(candidates) != 1:
            raise QueryError("location_ambiguous")
        source = candidates[0]
        state = live(source, "query", user, options)
    # Binding fields are validated by catalog construction and never derive services.
    observed_entity = binding.get("observed_at_entity") or preference.get("observed_at_entity")
    if observed_entity:
        observed_row = next((candidate for candidate in snapshot.by_id.values() if candidate["entity_id"] == observed_entity), None)
        if observed_row is None or observed_row["domain"] != "sensor" or observed_row.get("device_class") != "timestamp":
            raise QueryError("location_stale")
        observed = live(observed_row, "query", user, options).state
    else:
        attribute = binding.get("observed_at_attribute", preference.get("observed_at_attribute", "last_seen"))
        if attribute in ("last_changed", "last_updated", "last_reported"):
            raise QueryError("location_stale")
        observed = state.attributes.get(attribute)
    max_age = binding.get("max_age_seconds", preference.get("max_age_seconds", 300))
    if type(max_age) not in (int, float) or not 1 <= max_age <= 3600:
        raise QueryError("location_stale")
    age = observation_age(hass, observed, max_age)
    area_attribute = binding.get("area_attribute", preference.get("area_attribute", "area_id"))
    area_id = state.attributes.get(area_attribute)
    rooms = area_registry.async_get(hass).areas
    if type(area_id) is str and area_id in rooms:
        matches = [rooms[area_id]]
    elif area_id not in (None, ""):
        raise QueryError("location_unknown_area")
    else:
        matches = [room for aid, room in rooms.items() if normalize(state.state) in (normalize(aid), normalize(room.name), *(normalize(alias) for alias in getattr(room, "aliases", ())))]
    if not matches:
        raise QueryError("location_unknown_area")
    if len(matches) != 1:
        raise QueryError("location_ambiguous")
    label = row.get("attributes", {}).get("reference_device_name") or row.get("device_name") or row["name"]
    return {"label": label, "entity_id": row["entity_id"], "domain": row["domain"], "device_id": did,
            "area_id": None, "device_class": row.get("device_class"), "metric": "location", "unit": None,
            "value": matches[0].name, "observation_age_seconds": int(age)}
