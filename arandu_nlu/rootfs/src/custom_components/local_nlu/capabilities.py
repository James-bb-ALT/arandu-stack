"""Closed semantic adapters, discovered from registered services and entity features.

Feature flags and service fields were checked against the pinned public HA source
listed in docs/nlu-2.0/API-CONTRACTS.md. These are adapter definitions, never entity IDs.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

DOMAINS = frozenset("light fan climate media_player cover lock switch sensor binary_sensor person device_tracker scene script automation button input_button input_boolean number input_number select input_select humidifier water_heater vacuum lawn_mower camera alarm_control_panel timer calendar weather sun remote todo".split())


@dataclass(frozen=True)
class Adapter:
    service: str
    feature: int = 0
    field: str | None = None
    attribute: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    step: float = 1.0
    values: str | None = None
    read: bool = False


ADAPTERS: dict[tuple[str, str], Adapter] = {}


def _add(domains: str, action: str, service: str, **kwargs: Any) -> None:
    for domain in domains.split():
        ADAPTERS[(domain, action)] = Adapter(service, **kwargs)


for _domain in DOMAINS:
    _add(_domain, "query", "", read=True)
for _domain in "light switch input_boolean humidifier automation remote".split():
    _add(_domain, "turn_on", "turn_on")
    _add(_domain, "turn_off", "turn_off")
for _domain, _on, _off in [("fan", 32, 16), ("climate", 256, 128), ("media_player", 128, 256), ("water_heater", 8, 8), ("camera", 1, 1)]:
    _add(_domain, "turn_on", "turn_on", feature=_on)
    _add(_domain, "turn_off", "turn_off", feature=_off)
_add("light", "brightness", "turn_on", field="brightness_pct", attribute="brightness", minimum=0, maximum=100, step=10)
_add("light", "color", "turn_on", field="rgb_color")
_add("light", "color_temperature", "turn_on", field="color_temp_kelvin", attribute="color_temp_kelvin", minimum=2_000, maximum=6_500, step=250)
_add("light", "effect", "turn_on", feature=4, field="effect", values="effect_list")
_add("fan", "percentage", "set_percentage", feature=1, field="percentage", attribute="percentage", minimum=0, maximum=100, step=10)
_add("fan", "preset", "set_preset_mode", feature=8, field="preset_mode", values="preset_modes")
_add("fan", "oscillate", "oscillate", feature=2, field="oscillating")
_add("fan", "direction", "set_direction", feature=4, field="direction")
_add("climate", "temperature", "set_temperature", feature=1, field="temperature", attribute="temperature")
_add("climate", "hvac_mode", "set_hvac_mode", field="hvac_mode", values="hvac_modes")
_add("climate", "fan_mode", "set_fan_mode", feature=8, field="fan_mode", values="fan_modes")
_add("climate", "swing_mode", "set_swing_mode", feature=32, field="swing_mode", values="swing_modes")
_add("climate", "preset", "set_preset_mode", feature=16, field="preset_mode", values="preset_modes")
_add("humidifier", "humidity", "set_humidity", field="humidity", attribute="humidity", minimum=0, maximum=100, step=5)
_add("humidifier", "preset", "set_mode", feature=1, field="mode", values="available_modes")
_add("water_heater", "temperature", "set_temperature", feature=1, field="temperature", attribute="temperature")
_add("water_heater", "preset", "set_operation_mode", feature=2, field="operation_mode", values="operation_list")
for _action, _service, _feature in [("play", "media_play", 16384), ("pause", "media_pause", 1), ("stop", "media_stop", 4096), ("next", "media_next_track", 32), ("previous", "media_previous_track", 16)]:
    _add("media_player", _action, _service, feature=_feature)
_add("media_player", "volume", "volume_set", feature=4, field="volume_level", attribute="volume_level", minimum=0, maximum=100, step=10)
_add("media_player", "mute", "volume_mute", feature=8, field="is_volume_muted")
_add("media_player", "unmute", "volume_mute", feature=8, field="is_volume_muted")
_add("media_player", "source", "select_source", feature=2048, field="source", values="source_list")
_add("media_player", "seek", "media_seek", feature=2, field="seek_position", attribute="media_position", minimum=0, maximum=604_800)
for _action in ("shuffle_on", "shuffle_off"):
    _add("media_player", _action, "shuffle_set", feature=32768, field="shuffle")
for _action in ("repeat_one", "repeat_all", "repeat_off"):
    _add("media_player", _action, "repeat_set", feature=262144, field="repeat")
for _action, _service, _feature in [("open", "open_cover", 1), ("close", "close_cover", 2), ("cover_stop", "stop_cover", 8)]:
    _add("cover", _action, _service, feature=_feature)
_add("cover", "position", "set_cover_position", feature=4, field="position", attribute="current_position", minimum=0, maximum=100, step=10)
_add("lock", "lock", "lock")
_add("lock", "unlock", "unlock")
_add("scene script", "activate", "turn_on")
_add("button input_button", "press", "press")
_add("number input_number", "number", "set_value", field="value", attribute="__state__")
_add("select input_select", "select", "select_option", field="option", values="options")
_add("automation", "automation_enable", "turn_on")
_add("automation", "automation_disable", "turn_off")
for _action, _service, _feature in [("vacuum_start", "start", 8192), ("vacuum_stop", "stop", 8), ("vacuum_pause", "pause", 4), ("vacuum_dock", "return_to_base", 16), ("vacuum_area", "clean_area", 16384)]:
    _add("vacuum", _action, _service, feature=_feature, field="cleaning_area_id" if _action == "vacuum_area" else None)
for _action, _service, _feature in [("mower_start", "start_mowing", 1), ("mower_pause", "pause", 2), ("mower_dock", "dock", 4)]:
    _add("lawn_mower", _action, _service, feature=_feature)
_add("alarm_control_panel", "arm", "alarm_arm_home", feature=1)
_add("alarm_control_panel", "disarm", "alarm_disarm")
for _action, _service in [("timer_start", "start"), ("timer_resume", "start"), ("timer_pause", "pause"), ("timer_cancel", "cancel"), ("timer_finish", "finish"), ("timer_change", "change")]:
    _add("timer", _action, _service, field="duration" if _action in ("timer_start", "timer_change") else None)
_add("calendar", "calendar", "get_events", read=True)
_add("calendar", "calendar_create", "create_event", feature=1)
_add("weather", "forecast", "get_forecasts", feature=1, read=True)
_add("todo", "todo_add", "add_item", feature=1, field="item")
_add("todo", "todo_complete", "update_item", feature=4, field="item")
_add("todo", "todo_list", "get_items", read=True)

READ_ACTIONS = frozenset(action for (_, action), adapter in ADAPTERS.items() if adapter.read) | {"local_time", "local_date"}
SPECIAL_ACTIONS = frozenset(("music", "transfer", "remote_key", "channel", "camera_view", "binding", "cancel", "confirm", "repeat_response"))
ACTIONS = frozenset(action for _, action in ADAPTERS) | READ_ACTIONS | SPECIAL_ACTIONS


def finite(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def feature_supported(state: Any, adapter: Adapter) -> bool:
    features = state.attributes.get("supported_features", 0)
    return not adapter.feature or (type(features) is int and features >= 0 and features & adapter.feature == adapter.feature)


def capabilities(hass: Any, entity_id: str, state: Any, bindings: dict | None = None) -> list[str]:
    domain = entity_id.split(".", 1)[0]
    actions = []
    for (candidate, action), adapter in ADAPTERS.items():
        if candidate != domain or not feature_supported(state, adapter):
            continue
        if adapter.service and hass.services.has_service(domain, adapter.service) is not True:
            continue
        colors = state.attributes.get("supported_color_modes", [])
        if action == "brightness" and not any(mode not in ("onoff", "unknown") for mode in colors):
            continue
        if action == "color" and not any(mode in ("hs", "xy", "rgb", "rgbw", "rgbww") for mode in colors):
            continue
        if action == "color_temperature" and "color_temp" not in colors:
            continue
        if adapter.values and not state.attributes.get(adapter.values):
            continue
        actions.append(action)
    if domain == "media_player" and is_music_player(hass, entity_id):
        if hass.services.has_service("music_assistant", "search") and hass.services.has_service("music_assistant", "play_media"):
            actions.append("music")
        if hass.services.has_service("music_assistant", "transfer_queue"):
            actions.append("transfer")
    if bindings and entity_id in bindings:
        actions.extend(action for action in ("remote_key", "channel") if action in bindings[entity_id])
    if domain == "remote" and hass.services.has_service("remote", "send_command"):
        actions.append("remote_key")
    return sorted(set(actions))


def is_music_player(hass: Any, entity_id: str) -> bool:
    from homeassistant.helpers import entity_registry
    entry = entity_registry.async_get(hass).entities.get(entity_id)
    return entry is not None and getattr(entry, "platform", None) == "music_assistant"


MODE_ALIASES = {
    "automatico": "auto", "automatica": "auto", "automatico frio": "heat_cool", "resfriar": "cool", "frio": "cool",
    "aquecer": "heat", "quente": "heat", "ventilar": "fan_only", "ventilacao": "fan_only", "seco": "dry",
    "alta": "high", "alto": "high", "maxima": "high", "maximo": "high", "baixa": "low", "baixo": "low", "media": "medium",
    "desligado": "off", "ligado": "on", "para cima": "up", "para baixo": "down",
}
COLORS = {"azul": [0, 0, 255], "verde": [0, 128, 0], "vermelho": [255, 0, 0], "amarelo": [255, 255, 0],
          "branco": [255, 255, 255], "laranja": [255, 128, 0], "roxo": [128, 0, 128], "rosa": [255, 64, 128]}


class CapabilityError(Exception):
    """A semantic operation cannot be bound to a live safe service call."""


def parameters(action: str, adapter: Adapter, state: Any, params: dict, increments: dict) -> dict:
    """Validate typed slots and resolve relative values from live state, never snapshot values."""
    from .contextual_protocol import normalize
    value = params.get("value")
    relative = params.get("relative")
    if action in ("mute", "unmute"):
        return {"is_volume_muted": action == "mute"}
    if action.startswith("shuffle_"):
        return {"shuffle": action == "shuffle_on"}
    if action.startswith("repeat_"):
        return {"repeat": action.removeprefix("repeat_")}
    if action == "vacuum_area":
        area = params.get("area")
        if type(area) is not str:
            raise CapabilityError("missing_area")
        return {"cleaning_area_id": [area]}
    if action in ("timer_start", "timer_change"):
        duration = value if action == "timer_start" else relative
        if not finite(duration) or not 0 < abs(duration) <= 604_800 or int(duration) != duration:
            raise CapabilityError("duration")
        return {"duration": int(duration)}
    if adapter.field is None:
        if value is not None or relative is not None:
            raise CapabilityError("unexpected_parameter")
        return {}
    if action == "color":
        color = normalize(value) if type(value) is str else ""
        color = {"vermelha": "vermelho", "amarela": "amarelo", "branca": "branco", "roxa": "roxo"}.get(color, color)
        if color not in COLORS:
            raise CapabilityError("color")
        return {adapter.field: COLORS[color]}
    if action == "oscillate":
        if type(value) is not bool:
            raise CapabilityError("boolean")
        return {adapter.field: value}
    if action == "direction":
        value = {"frente": "forward", "reverso": "reverse"}.get(value, value)
        if value not in ("forward", "reverse"):
            raise CapabilityError("direction")
        return {adapter.field: value}
    if adapter.values:
        values = state.attributes.get(adapter.values)
        if type(value) is not str or not isinstance(values, (list, tuple)):
            raise CapabilityError("mode")
        aliases = {**MODE_ALIASES, **({"cima": "up", "baixo": "down", "fixo": "off", "oscilando": "on", "automatico": "on"} if action == "swing_mode" else {})}
        value = aliases.get(normalize(value), value)
        matches = [candidate for candidate in values if type(candidate) is str and normalize(candidate) == normalize(value)]
        if len(matches) != 1:
            raise CapabilityError("unsupported_mode")
        return {adapter.field: matches[0]}
    if action in ("todo_add", "todo_complete"):
        if type(value) is not str or not value.strip() or len(value.encode()) > 256:
            raise CapabilityError("item")
        return {"item": value, **({"status": "completed"} if action == "todo_complete" else {})}
    qualitative = value if type(value) is str and value in ("minimum", "maximum", "half", "low", "medium", "high") else None
    if not finite(value) and qualitative is None and relative is None:
        raise CapabilityError("number")
    attrs = state.attributes
    low, high = adapter.minimum, adapter.maximum
    step = attrs.get("step", attrs.get("target_temp_step", 1))
    if action in ("number", "temperature", "humidity", "color_temperature"):
        keys = {"number": ("min", "max"), "temperature": ("min_temp", "max_temp"), "humidity": ("min_humidity", "max_humidity"), "color_temperature": ("min_color_temp_kelvin", "max_color_temp_kelvin")}[action]
        low, high = attrs.get(keys[0], low), attrs.get(keys[1], high)
    if action == "seek":
        high = attrs.get("media_duration", high)
    if not finite(low) or not finite(high) or low > high:
        raise CapabilityError("missing_limits")
    if qualitative:
        minimum = attrs.get("percentage_step", 1) if action == "percentage" else 1 if action == "brightness" else low
        if not finite(minimum) or not low <= minimum <= high:
            raise CapabilityError("minimum")
        value = minimum if qualitative == "minimum" else high if qualitative == "maximum" else low + (high - low) * {"half": .5, "low": .25, "medium": .5, "high": .75}[qualitative]
    if relative is not None:
        current = state.state if adapter.attribute == "__state__" else attrs.get(adapter.attribute)
        try:
            current = float(current)
        except (TypeError, ValueError):
            raise CapabilityError("missing_current_value") from None
        if not math.isfinite(current):
            raise CapabilityError("missing_current_value")
        if action == "brightness":
            current = current / 255 * 100
        if action == "volume":
            current *= 100
        delta = value if finite(value) else increments.get(action, adapter.step)
        if not finite(delta) or delta <= 0 or delta > 1000:
            raise CapabilityError("increment")
        value = min(high, max(low, current + relative * delta))
    if not finite(value) or not low <= value <= high:
        raise CapabilityError("range")
    if action in ("number", "temperature") and finite(step) and step > 0:
        value = low + round((value - low) / step) * step
        value = min(high, max(low, value))
    if action == "percentage" and value > 0:
        step = attrs.get("percentage_step", 1)
        if not finite(step) or not 0 < step <= 100:
            raise CapabilityError("percentage_step")
        value = min(high, max(step, round(value / step) * step))
    if action == "volume":
        value /= 100
    if action in ("position", "brightness", "percentage", "humidity", "color_temperature"):
        value = round(value)
    return {adapter.field: value}


def sensitive(domain: str, action: str, state: Any) -> bool:
    return (domain == "lock" and action == "unlock") or (domain == "alarm_control_panel" and action == "disarm") or (domain == "cover" and action in ("open", "position") and state.attributes.get("device_class") in ("gate", "garage", "door"))
