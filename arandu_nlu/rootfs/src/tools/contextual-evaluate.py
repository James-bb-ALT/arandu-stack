#!/usr/bin/env python3
"""Corpus diagnostic: real Rust HTTP, real executor code, simulated HA devices.

Reads external MIT CSV with csv.DictReader. Report separates recognition,
semantic plans and simulated execution; never calls this generalization accuracy
or live Home Assistant validation.
"""
from __future__ import annotations
import argparse
import asyncio
import collections
import csv
import json
import re
from pathlib import Path
import sys
import time
from datetime import timedelta
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests/mlp")]
from test_contextual import Client, EndToEndTests, HttpClient, add, hass_, input_  # noqa: E402
from custom_components.local_nlu.capabilities import ADAPTERS  # noqa: E402
from custom_components.local_nlu.contextual_runtime import ContextualRuntime, Session  # noqa: E402
from custom_components.local_nlu.contextual_catalog import build  # noqa: E402
from custom_components.local_nlu.contextual_protocol import normalize  # noqa: E402
from custom_components.local_nlu.contextual_protocol import parse as parse_response  # noqa: E402
from custom_components.local_nlu.queries import now  # noqa: E402


def slot_checks(row: dict, spec: dict, operations: list) -> tuple[bool, int]:
    """Compare source slots independently; no engine output becomes expected data."""
    if not operations:
        return True, 0
    params = operations[0]["parameters"]
    slots = dict(item.split("=", 1) for item in row["slots_exemplo"].split("|") if "=" in item)
    checks = []
    if spec.get("metric"):
        checks.append(params.get("metric") == spec["metric"])
    if spec.get("relative") is not None:
        actual_relative = params.get("relative")
        checks.append(type(actual_relative) in (int, float) and (actual_relative * spec["relative"] > 0 if spec["operation"] in ("timer_change", "seek") else actual_relative == spec["relative"]))
    source_slot = spec.get("value_slot")
    expected = slots.get(source_slot)
    if expected is not None and spec["operation"] not in ("transfer", "binding", "calendar_create"):
        actual = params.get("date") if source_slot == "date_ref" else params.get("value")
        if type(actual) in (int, float):
            cleaned = normalize(expected).removesuffix(" por cento").removesuffix(" graus").removesuffix(" kelvin")
            lexical = {"um": 1, "dois": 2, "dez": 10, "vinte": 20, "vinte e tres": 23, "cinquenta": 50, "metade": 50, "baixa": 25, "media": 50, "alta": 75, "maxima": 100, "maximo": 100, "minima": 1, "minimo": 1}
            numeric = float(cleaned) if re.fullmatch(r"\d+", cleaned) else lexical.get(cleaned)
            if numeric is not None:
                checks.append(actual == numeric)
        elif type(actual) is str:
            quality = {"metade": "half", "maxima": "maximum", "maximo": "maximum", "minima": "minimum", "minimo": "minimum", "alta": "high", "baixa": "low", "media": "medium"}
            checks.append(normalize(actual) == (quality.get(normalize(expected), normalize(expected)) if actual in ("half", "maximum", "minimum", "low", "medium", "high") else normalize(expected)))
    if spec["operation"] == "music":
        expected = next((slots[key] for key in ("media", "artist", "playlist") if key in slots), None)
        if expected:
            checks.append(type(params.get("value")) is str and normalize(params["value"]) == normalize(expected))
    return all(checks), len(checks)


ATTRS = {
    "supported_features": 8_388_607, "supported_color_modes": ["brightness", "rgb", "color_temp"],
    "brightness": 128, "percentage": 50, "percentage_step": 1, "temperature": 22, "current_temperature": 25,
    "min_temp": 7, "max_temp": 35, "target_temp_step": .5, "min": 0, "max": 100, "step": 1,
    "min_humidity": 0, "max_humidity": 100, "humidity": 50, "volume_level": .5,
    "current_position": 50, "media_position": 10, "media_duration": 300, "color_temp_kelvin": 4000,
    "min_color_temp_kelvin": 2000, "max_color_temp_kelvin": 6500,
    "hvac_modes": ["off", "auto", "cool", "heat", "dry", "fan_only"], "fan_modes": ["low", "medium", "high", "auto"],
    "swing_modes": ["off", "on", "up", "down"], "preset_modes": ["eco", "auto"],
    "source_list": ["Spotify", "YouTube", "HDMI 2"], "media_title": "FIXTURE_TECNICA_TRACK", "code_arm_required": False,
    "message": "FIXTURE_TECNICA_EVENT", "start_time": "2026-10-06T10:00:00-03:00", "next_rising": "2026-10-07T08:00:00+00:00", "next_setting": "2026-10-06T21:00:00+00:00",
}


def fixture(row: dict, spec: dict):
    hass = hass_()
    # The corpus test environment has only the relevant entity. Mixed catalog
    # safety/ambiguity is independently tested by test_contextual.py.
    hass.entity_registry.entities.clear()
    hass.states._states.clear()
    hass.exposed.clear()
    hass.area_registry.areas.clear()
    slots = dict(item.split("=", 1) for item in row["slots_exemplo"].split("|") if "=" in item)
    area = slots.get("area", slots.get("media_destination", "sala"))
    room_names = {"sala", "quarto", "cozinha", "banheiro", "varanda", "escritório", area}
    for i, name in enumerate(sorted(room_names)):
        hass.area_registry.areas[f"room_{i}"] = type("Area", (), {"name": name, "aliases": set()})()
    area_id = next(aid for aid, item in hass.area_registry.areas.items() if item.name == area)
    hass.floor_registry = SimpleNamespace(floors={})
    if "floor_group" in slots:
        hass.floor_registry.floors["floor_fixture"] = SimpleNamespace(name=slots["floor_group"], aliases=set())
        hass.area_registry.areas[area_id].floor_id = "floor_fixture"
    sys.modules["homeassistant.helpers.floor_registry"] = SimpleNamespace(async_get=lambda current: current.floor_registry)
    domains = spec.get("domains", [])
    domain = domains[0] if domains else "script" if spec["operation"] == "binding" else "light"
    if "group_type" in slots:
        domain = {"ventiladores": "fan", "tomadas": "switch", "cortinas": "cover", "persianas": "cover"}.get(normalize(slots["group_type"]), "light")
    target_keys = [spec.get("target_slot", ""), "media_player", "light", "fan", "climate", "cover", "lock", "gate", "door", "window", "plug", "humidifier", "dehumidifier", "vacuum", "mower", "person", "scene", "routine", "device", "camera", "appliance"]
    target = next((slots[key] for key in target_keys if key in slots), None)
    defaults = {"light": "Luz", "fan": "Ventilador", "climate": "Ar-condicionado", "media_player": "TV da sala", "cover": "Portão" if row["intent"].startswith("gate.") else "Cortina", "lock": "Fechadura", "sensor": "Sensor", "script": "Rotina", "humidifier": "Umidificador", "timer": "Timer"}
    name = target or defaults.get(domain, domain)
    # Source names such as "ventilador do quarto" describe the fixture's real
    # room. Do not register them in the default Sala and contradict the input.
    if "area" not in slots and target:
        for candidate_id, candidate in hass.area_registry.areas.items():
            if any(normalize(target).endswith(prefix + normalize(candidate.name)) for prefix in (" do ", " da ", " de ")):
                area_id = candidate_id
                break
    if not domains and target:
        for word, candidate in [("ventilador", "fan"), ("televis", "media_player"), ("ar condicionado", "climate"), ("luz", "light"), ("abajur", "light")]:
            if word in normalize(name):
                domain = candidate
                break
    attrs = dict(ATTRS)
    attrs["device_class"] = spec.get("device_class") or ("gate" if row["intent"].startswith("gate.") else "door" if row["intent"] in ("door.status", "security.openings_any_open") else "window" if row["intent"] == "window.status" else "outlet" if row["intent"].startswith("plug.") else "tv" if domain == "media_player" else None)
    if spec.get("metric") in ("tank_level", "people_count", "remaining_time"):
        attrs["semantic_category"] = spec["metric"]
    if spec.get("metric") in ("power", "energy", "battery", "humidity", "temperature", "noise", "co2", "air_quality", "illuminance"):
        attrs["unit_of_measurement"] = {"power": "W", "energy": "kWh", "battery": "%", "humidity": "%", "temperature": "°C", "co2": "ppm", "illuminance": "lx", "noise": "dB", "air_quality": "AQI"}[spec["metric"]]
    if row["intent"].startswith("dehumidifier."):
        attrs["device_class"] = "dehumidifier"
    if row["intent"].startswith("humidifier."):
        attrs["device_class"] = "humidifier"
    entity_id = f"{domain}.fixture"
    state = "24.5" if domain == "sensor" else area if domain in ("person", "device_tracker") else "on"
    entry = add(hass, "target", entity_id, name, attrs, state, area_id)
    entry.platform = "music_assistant" if domain == "media_player" else "fixture"
    entry.config_entry_id = "FIXTURE_TECNICA_MA"
    entry.device_id = "FIXTURE_TECNICA_DEVICE"
    hass.device_registry.async_get = lambda _: type("Device", (), {"name": name, "name_by_user": None, "area_id": area_id})()
    hass.services.available |= {(candidate, adapter.service) for (candidate, _), adapter in ADAPTERS.items() if adapter.service}
    hass.services.available |= {("music_assistant", service) for service in ("search", "play_media", "transfer_queue")}
    options = {"default_area": area_id, "sensitive_entities": [entity_id], "energy_sources": [entity_id]}
    if spec["operation"] in ("binding", "camera_view"):
        backend = add(hass, "binding", "script.fixture", "FIXTURE_TECNICA_ADAPTER", {}, "off", area_id)
        options["intent_bindings"] = {row["intent"]: {"entity_id": "script.fixture", "sensitive": False, "variables": {key: key for key in ("summary", "date", "time", "message", "contact", "person", "camera", "area")}}}
    # Public return_response service contracts, no invented cloud behavior.
    original_call = hass.services.async_call
    async def call(domain_, service, data, **kwargs):
        kwargs.setdefault("target", {})
        kwargs.pop("return_response", None)
        await original_call(domain_, service, data, **kwargs)
        if service == "get_events":
            return {entity_id: {"events": []}}
        if service == "get_forecasts":
            # HA get_forecasts returns a horizon, not the requested NLU date.
            return {entity_id: {"forecast": [{"datetime": (now(hass) + timedelta(days=offset)).isoformat(), "condition": "sunny", "temperature": 25} for offset in range(8)]}}
        if service == "search":
            kind = data["media_type"][0]
            return {kind + "s": [{"uri": "spotify://" + kind + "/fixture", "name": data["name"]}]}
    hass.services.async_call = call
    if spec["operation"] == "transfer":
        source = add(hass, "source", "media_player.source", "FIXTURE_TECNICA_SOURCE", dict(ATTRS), "playing", None)
        source.platform, source.config_entry_id = "music_assistant", "FIXTURE_TECNICA_MA"
    if domain in ("person", "device_tracker") and spec.get("metric") == "location":
        add(hass, "tracker", "sensor.indoor_fixture", "FIXTURE_TECNICA_INDOOR", {}, area_id, None)
        options["entity_preferences"] = {entity_id: {"indoor_tracker": "sensor.indoor_fixture"}}
    return hass, options


async def evaluate(rows: list[dict], mapping: dict, endpoint: str) -> dict:
    results = []
    client = HttpClient(endpoint)
    for row in rows:
        spec = mapping[row["intent"]]
        hass, options = fixture(row, spec)
        snapshot = build(hass, hass.auth.user, options)
        await client.async_catalog_v4(snapshot.payload)
        raw = await client.async_interpret_v4({"version": 4, "text": row["frase_exemplo"], "generation": snapshot.payload["generation"], "origin_area": options["default_area"], "last_targets": ["source"] if spec["operation"] == "transfer" else []})
        recognized = raw["status"] not in ("invalid_request", "no_match", "stale")
        actions = [operation["action"] for operation in raw.get("operations", [])]
        planning = raw["status"] == "plan" and bool(parse_response(raw).operations)
        expected_targets = [] if spec["operation"] in ("binding", "camera_view", "local_time", "local_date") else ["target"]
        targets_correct = not planning or all(operation["targets"] == expected_targets for operation in raw["operations"])
        semantic = actions == [spec["operation"]] or raw["status"] == spec["operation"] or (raw["status"] == "cancel" and row["intent"] == "assistant.deny")
        slots_correct, verified_slots = slot_checks(row, spec, raw.get("operations", []))
        semantic = semantic and slots_correct and targets_correct
        runtime = ContextualRuntime(hass, Client(raw), lambda: options)
        if spec["operation"] == "transfer":
            runtime.sessions[("user", "text", "fixture")] = Session(time.monotonic() + 60, last_targets=("source",))
        result = await runtime.process(input_(row["frase_exemplo"]))
        if result.code == "confirmation_required":
            result = await runtime.process(input_("Sim"))
        operational = semantic and result.code in ("success", "query_success", "cancelled")
        results.append({"row_id": row["id"], "intent": row["intent"], "linguistic": recognized, "semantic": semantic, "planning": planning, "targets_correct": targets_correct, "slots_correct": slots_correct, "verified_slots": verified_slots, "operational_simulated": operational, "nlu_status": raw["status"], "runtime_status": result.code, "reason": result.reason or raw.get("reason"), "actions": actions, "parameters": [op["parameters"] for op in raw.get("operations", [])]})
    totals = {key: sum(result[key] for result in results) for key in ("linguistic", "semantic", "planning", "operational_simulated")}
    by_intent = {}
    for intent in sorted(mapping):
        group = [result for result in results if result["intent"] == intent]
        by_intent[intent] = {"cases": len(group), **{key: sum(result[key] for result in group) for key in totals}, "statuses": dict(collections.Counter(result["runtime_status"] for result in group)), "reasons": sorted({result["reason"] for result in group if result["reason"]})}
    return {"kind": "diagnostic internal STT corpus conformance, HA simulated; not live HA or independent accuracy", "cases": len(results), "totals": totals, "intents": by_intent, "cases_detail": results}


def main():
    parser = argparse.ArgumentParser()
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--stt-root", type=Path)
    inputs.add_argument("--corpus-csv", type=Path, help="Frozen corpus CSV; avoids requiring a sibling checkout")
    parser.add_argument("--output", type=Path, default=ROOT / "target/contextual-coverage.json")
    parser.add_argument("--representative", action="store_true")
    args = parser.parse_args()
    source = args.corpus_csv or args.stt_root / "arandu_stt/datasets/commands/arandu_comandos_comuns_ptbr_v2.csv"
    with source.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file, strict=True))
    if args.representative:
        rows = list({row["intent"]: row for row in reversed(rows)}.values())
    grammar = json.loads((ROOT / "addon/engine/data/grammar.json").read_text(encoding="utf-8"))
    mapping = {rule["intent"]: rule for rule in grammar}
    EndToEndTests.setUpClass()
    try:
        report = asyncio.run(evaluate(rows, mapping, EndToEndTests.endpoint))
    finally:
        EndToEndTests.tearDownClass()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"cases": report["cases"], "totals": report["totals"]}))
    for intent, result in report["intents"].items():
        if result["operational_simulated"] != result["cases"]:
            print(intent, result)


if __name__ == "__main__":
    main()
