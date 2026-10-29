"""Read-only query engine with semantic measurements and unit-safe aggregation."""
from __future__ import annotations

from datetime import datetime, timedelta
import math
from typing import Any
from zoneinfo import ZoneInfo
import re

from .contextual_protocol import normalize


class QueryError(Exception):
    """Missing evidence or incompatible readings prevents a truthful answer."""


def now(hass: Any) -> datetime:
    return datetime.now(ZoneInfo(getattr(getattr(hass, "config", None), "time_zone", "America/Bahia")))


def calendar_parameters(hass: Any, value: Any) -> dict:
    if type(value) is not dict or type(value.get("summary")) is not str or not value["summary"].strip():
        raise QueryError("calendar_summary")
    try:
        if "start" in value and "end" in value:
            start, end = datetime.fromisoformat(value["start"]), datetime.fromisoformat(value["end"])
        else:
            day, _ = date_window(hass, value.get("date"))
            time_text = normalize(value.get("time", ""))
            time_text = re.sub(r"^(as |a |pelas )", "", time_text).replace(" horas", "").strip()
            words = {"uma": 1, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7, "oito": 8, "nove": 9, "dez": 10, "onze": 11, "doze": 12, "treze": 13, "catorze": 14, "quatorze": 14, "quinze": 15, "dezesseis": 16, "dezessete": 17, "dezoito": 18, "dezenove": 19, "vinte": 20, "vinte e uma": 21, "vinte e duas": 22, "vinte e tres": 23}
            period = ""
            for suffix in (" da manha", " da tarde", " da noite"):
                if time_text.endswith(suffix):
                    period, time_text = suffix, time_text.removesuffix(suffix)
            minute = 0
            for suffix, amount in ((" e meia", 30), (" e quinze", 15), (" e trinta", 30)):
                if time_text.endswith(suffix):
                    time_text, minute = time_text.removesuffix(suffix), amount
            if time_text == "meio dia":
                hour = 12
            elif time_text in words:
                hour = words[time_text]
            else:
                match = re.fullmatch(r"(\d{1,2})(?: h ?| )(\d{2})|\d{1,2}", time_text.removesuffix("h"))
                if match is None:
                    raise ValueError
                hour, minute = int(match[1] or match[0]), int(match[2] or 0)
            if period in (" da tarde", " da noite") and 1 <= hour < 12:
                hour += 12
            elif period == " da manha" and hour == 12:
                hour = 0
            start = datetime.fromisoformat(day).replace(hour=hour, minute=minute)
            duration = value.get("duration")
            if type(duration) not in (int, float) or not math.isfinite(duration) or not 0 < duration <= 604800:
                raise ValueError
            end = start + timedelta(seconds=duration)
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise ValueError
    except (ValueError, TypeError, KeyError):
        raise QueryError("calendar_datetime") from None
    return {"summary": value["summary"], "start_date_time": start.isoformat(), "end_date_time": end.isoformat()}


def number(value: Any) -> float:
    if type(value) is bool:
        raise QueryError("invalid_measurement")
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise QueryError("invalid_measurement") from None
    if not math.isfinite(result):
        raise QueryError("invalid_measurement")
    return result


def decimal(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".").replace(".", ",")


def read(hass: Any, row: dict, state: Any, params: dict) -> dict:
    attrs = state.attributes
    metric = params.get("metric")
    value, unit = state.state, attrs.get("unit_of_measurement")
    if metric in ("temperature", "target_temperature", "device_temperature"):
        if row["domain"] == "climate":
            value = attrs.get("temperature" if metric == "target_temperature" else "current_temperature")
            unit = attrs.get("temperature_unit") or getattr(getattr(getattr(hass, "config", None), "units", None), "temperature_unit", "°C")
        value = number(value)
    elif metric == "speed":
        value, unit = number(attrs.get("percentage")), "%"
    elif metric == "position":
        value, unit = number(attrs.get("current_position")), "%"
    elif metric in ("power", "energy", "humidity", "battery", "noise", "illuminance", "co2", "air_quality", "tank_level", "people_count"):
        value = number(value)
    elif metric == "media":
        value = attrs.get("media_title")
        if type(value) is not str or not value:
            raise QueryError("missing_media_title")
    elif metric == "area":
        area_id = row.get("area_id")
        from homeassistant.helpers import area_registry
        area = area_registry.async_get(hass).areas.get(area_id)
        if area is None:
            raise QueryError("unknown_area")
        value = area.name
    elif metric in ("sunrise", "sunset"):
        value = attrs.get("next_rising" if metric == "sunrise" else "next_setting")
    elif metric == "next_event":
        value = attrs.get("message")
        start = attrs.get("start_time")
        if type(value) is not str or type(start) is not str:
            raise QueryError("no_calendar_event")
        value = f"{value}, {start}"
    elif metric == "weather":
        condition = {"sunny": "ensolarado", "rainy": "chuvoso", "cloudy": "nublado", "partlycloudy": "parcialmente nublado", "clear-night": "céu limpo"}.get(state.state, state.state)
        temperature = attrs.get("temperature")
        value = condition + (f", {decimal(number(temperature))} {attrs.get('temperature_unit', '')}" if temperature is not None else "")
    elif metric == "remaining":
        if state.state == "active":
            finish = attrs.get("finishes_at")
            if type(finish) is not str:
                raise QueryError("missing_remaining")
            try:
                end = datetime.fromisoformat(finish.replace("Z", "+00:00"))
                value = max(0, round((end - now(hass)).total_seconds()))
            except (ValueError, TypeError):
                raise QueryError("invalid_remaining") from None
            unit = "s"
        elif state.state == "paused":
            value = attrs.get("remaining")
        else:
            value = "sem timer ativo"
    if value is None or not isinstance(value, (str, int, float)) or len(str(value).encode()) > 512:
        raise QueryError("invalid_reading")
    units = {"temperature": {"°C", "°F", "K"}, "target_temperature": {"°C", "°F", "K"}, "device_temperature": {"°C", "°F", "K"}, "humidity": {"%"}, "battery": {"%"}, "illuminance": {"lx", "lux"}, "co2": {"ppm"}, "noise": {"dB", "dBA"}, "air_quality": {"AQI"}}
    if metric in units and unit not in units[metric]:
        raise QueryError("incompatible_units")
    if metric in ("humidity", "battery") and not 0 <= value <= 100:
        raise QueryError("invalid_measurement")
    if metric in ("illuminance", "co2", "air_quality") and value < 0:
        raise QueryError("invalid_measurement")
    if metric in ("temperature", "target_temperature", "device_temperature") and value < {"°C": -273.15, "°F": -459.67, "K": 0}[unit]:
        raise QueryError("invalid_measurement")
    return {"label": row["name"], "entity_id": row["entity_id"], "domain": row["domain"], "device_class": row.get("device_class"), "device_id": row.get("device_id"), "area_id": row.get("area_id"), "value": value, "unit": unit, "metric": metric}


def render(values: list[dict], params: dict, options: dict) -> str:
    aggregate = params.get("aggregate")
    metric = params.get("metric")
    if not values:
        raise QueryError("no_readings")
    selected = list(values)
    if params.get("state_filter"):
        expected = params["state_filter"]
        def state_matches(row: dict) -> bool:
            actual = row["value"]
            domain = row.get("domain")
            # A motion detection or open cover is not a powered appliance.
            if metric is None and expected in ("on", "off") and domain in ("sensor", "binary_sensor", "cover", "person", "device_tracker", "sun", "calendar", "weather"):
                return False
            if domain == "media_player" and expected in ("on", "off"):
                return actual in (("on", "idle", "playing", "paused", "buffering") if expected == "on" else ("off", "standby"))
            if expected == "on":
                return actual in ("on", "open", "playing", "paused", "cool", "heat", "heat_cool", "auto", "fan_only", "dry")
            if expected == "off":
                return actual in ("off", "closed", "idle", "standby")
            return actual == expected
        selected = [row for row in values if state_matches(row)]
    elif metric in ("opening", "presence", "leak"):
        selected = [row for row in values if row["value"] in ("on", "open")]
    elif metric == "battery" and aggregate == "filter":
        threshold = options.get("low_battery_threshold", params.get("threshold", 20))
        if type(threshold) not in (int, float) or not math.isfinite(threshold) or not 0 <= threshold <= 100:
            raise QueryError("invalid_measurement")
        selected = [row for row in values if number(row["value"]) < threshold]
    if aggregate == "count":
        return f"Encontrei {len(selected)}."
    if aggregate == "any":
        return "Sim. " + render_list(selected) if selected else "Não. Não encontrei dispositivos correspondentes ligados." if params.get("state_filter") == "on" else "Não. Nenhum dispositivo corresponde à consulta."
    if aggregate == "all":
        return "Sim, todos." if len(selected) == len(values) else "Não, nem todos."
    if aggregate == "none":
        return "Sim, nenhuma." if not selected else "Não. " + render_list(selected)
    if aggregate == "filter":
        return render_list(selected) if selected else "Nenhum dispositivo corresponde à consulta."
    if aggregate in ("sum", "average", "min", "max", "compare"):
        units = {row["unit"] for row in values}
        if len(units) != 1:
            raise QueryError("incompatible_units")
        numbers = [number(row["value"]) for row in values]
        if aggregate == "sum":
            admitted = options.get("energy_sources", [])
            if metric in ("power", "energy"):
                if not admitted or set(admitted) != {row["entity_id"] for row in values} or len(admitted) != len(set(admitted)):
                    raise QueryError("energy_sources_not_verified")
                devices = [row["device_id"] for row in values if row["device_id"]]
                if len(devices) != len(set(devices)):
                    raise QueryError("duplicate_energy_source")
            result = sum(numbers)
        elif aggregate == "average":
            result = sum(numbers) / len(numbers)
        elif aggregate in ("min", "max"):
            result = min(numbers) if aggregate == "min" else max(numbers)
            return "; ".join(render_one(row) for row in values if number(row["value"]) == result) + "."
        else:
            threshold = params.get("threshold", options.get("hot_temperature", 26))
            if type(threshold) not in (int, float) or not math.isfinite(threshold):
                raise QueryError("comparison_threshold")
            return f"{'Sim' if any(value > threshold for value in numbers) else 'Não'}, {render_one(values[0])}."
        return f"O valor {'total' if aggregate == 'sum' else 'médio'} é {decimal(result)} {values[0]['unit'] or ''}."
    return "; ".join(render_one(row) for row in values) + "."


def render_one(row: dict) -> str:
    metric, value = row["metric"], row["value"]
    label = row["label"]
    if row.get("area_name"):
        label += f" ({row['area_name']})"
    unit = row["unit"]
    if metric in ("temperature", "target_temperature", "device_temperature"):
        prefix = "A temperatura-alvo" if metric == "target_temperature" else "A temperatura"
        scope = f"em {row['scope_label']}" if row.get("scope_label") else f"de {label}"
        return f"{prefix} {scope} está em {decimal(number(value))} {'graus' if unit == '°C' else unit or ''}"
    if metric == "location":
        location = {"home": "em casa", "not_home": "fora de casa"}.get(value, f"em {value}")
        age = f", observado há {row['observation_age_seconds']} segundos" if "observation_age_seconds" in row else ""
        return f"{label} está {location}{age}"
    if metric == "area":
        return f"{label} fica em {value}"
    if metric in ("opening", "presence", "leak"):
        positive = value in ("on", "open")
        words = {"opening": ("aberto", "fechado"), "presence": ("detectou presença", "não detectou presença"), "leak": ("detectou vazamento", "não detectou vazamento")}
        return f"{label}: {words[metric][0 if positive else 1]}"
    display = decimal(value) if type(value) in (int, float) else {"on": "ligado", "off": "desligado", "open": "aberto", "closed": "fechado", "locked": "trancado", "unlocked": "destrancado", "playing": "tocando", "paused": "pausado"}.get(value, value)
    return f"{label}: {display}{' ' + unit if type(unit) is str else ''}"


def render_list(values: list[dict]) -> str:
    visible = values[:6]
    suffix = f"; e mais {len(values) - len(visible)} dispositivos" if len(values) > len(visible) else ""
    return "; ".join(render_one(row) for row in visible) + suffix + "."


def date_window(hass: Any, reference: str | None) -> tuple[str, str]:
    current = now(hass)
    ref = normalize(reference or "hoje")
    if ref == "hoje":
        day = current
    elif ref == "amanha":
        day = current + timedelta(days=1)
    elif ref in ("depois de amanha", "depois amanha"):
        day = current + timedelta(days=2)
    else:
        days = {"segunda feira": 0, "terca feira": 1, "quarta feira": 2, "quinta feira": 3, "sexta feira": 4, "sabado": 5, "domingo": 6}
        if ref not in days:
            raise QueryError("unsupported_date")
        day = current + timedelta(days=(days[ref] - current.weekday()) % 7)
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.isoformat(), (start + timedelta(days=1)).isoformat()


def calendar_response(raw: Any, params: dict) -> str:
    if type(raw) is not dict:
        raise QueryError("calendar_response")
    events = []
    for result in raw.values():
        if type(result) is not dict or type(result.get("events")) is not list:
            raise QueryError("calendar_response")
        for event in result["events"]:
            if type(event) is not dict or type(event.get("summary")) is not str or type(event.get("start")) is not str:
                raise QueryError("calendar_event")
            title = params.get("value")
            if type(title) is str and normalize(title) not in normalize(event["summary"]):
                continue
            events.append((event["start"], event["summary"]))
    if len(events) > 32:
        raise QueryError("calendar_limit")
    return "; ".join(f"{title}, {start}" for start, title in sorted(events)) + "." if events else "Não encontrei compromissos nesse período."
