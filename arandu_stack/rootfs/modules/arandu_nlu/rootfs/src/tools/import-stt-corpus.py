#!/usr/bin/env python3
"""Reproducible STT lexical inventory. This is conformance, not an accuracy gold set.

The source stays outside the NLU runtime. MIT upstream notice accompanies the
derived templates. Semantic dispositions below are reviewed independently of CSV labels.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent


def dispositions() -> dict[str, dict]:
    result: dict[str, dict] = {}

    def add(ids: str, operation: str, domains: str = "", **extra: object) -> None:
        for intent in ids.split():
            result[intent] = {"operation": operation, "domains": domains.split(), **extra}

    for kind, device_class in {
        "temperature": "temperature", "humidity": "humidity", "co2": "carbon_dioxide",
        "illuminance": "illuminance", "noise": "sound_pressure", "air_quality": "aqi",
    }.items():
        add(f"sensor.{kind}.query", "query", "sensor climate" if kind == "temperature" else "sensor", device_class=device_class, metric=kind)
    add("presence.area.query", "query", "binary_sensor", device_class="occupancy", aggregate="any", metric="presence")
    add("presence.count.query", "query", "sensor", device_class="", metric="people_count")
    add("presence.person_area.query presence.person_location.query", "query", "person device_tracker", metric="location")
    for family, domain, slot in [
        ("light", "light", "light"), ("fan", "fan", "fan"), ("climate", "climate", "climate"),
        ("device", "", "device"), ("plug", "switch", "plug"), ("humidifier", "humidifier", "humidifier"),
        ("dehumidifier", "humidifier", "dehumidifier"),
    ]:
        add(f"{family}.turn_on", "turn_on", domain, target_slot=slot)
        add(f"{family}.turn_off", "turn_off", domain, target_slot=slot)
        add(f"{family}.status", "query", domain, target_slot=slot)
    add("light.all_on group.turn_on", "turn_on", "light", plural=True)
    add("light.all_off group.turn_off", "turn_off", "light", plural=True)
    add("light.brightness_set", "brightness", "light", value_slot="brightness")
    add("light.brightness_up", "brightness", "light", relative=1)
    add("light.brightness_down", "brightness", "light", relative=-1)
    add("light.color_set", "color", "light", value_slot="color")
    add("light.color_temperature_set", "color_temperature", "light", value_slot="kelvin")
    add("light.color_temperature_warmer", "color_temperature", "light", relative=-1)
    add("light.color_temperature_cooler", "color_temperature", "light", relative=1)
    add("fan.speed_set", "percentage", "fan", value_slot="fan_speed")
    add("fan.speed_up", "percentage", "fan", relative=1)
    add("fan.speed_down", "percentage", "fan", relative=-1)
    add("climate.temperature_set", "temperature", "climate", value_slot="temperature")
    add("climate.mode_set", "hvac_mode", "climate", value_slot="hvac_mode")
    add("climate.fan_speed_set", "fan_mode", "climate", value_slot="fan_speed")
    add("climate.swing_set", "swing_mode", "climate", value_slot="swing_mode")
    add("humidifier.humidity_set dehumidifier.humidity_set", "humidity", "humidifier", value_slot="humidity_target")
    for intent, op in {
        "pause": "pause", "resume": "play", "stop": "stop", "next": "next", "previous": "previous",
        "mute": "mute", "unmute": "unmute", "shuffle_on": "shuffle_on", "shuffle_off": "shuffle_off",
        "repeat_one": "repeat_one", "repeat_all": "repeat_all", "repeat_off": "repeat_off",
    }.items():
        add(f"media.{intent}", op, "media_player")
    add("media.volume_up satellite.volume_up", "volume", "media_player", relative=1)
    add("media.volume_down satellite.volume_down", "volume", "media_player", relative=-1)
    add("media.volume_set satellite.volume_set", "volume", "media_player", value_slot="volume")
    add("satellite.mute", "mute", "media_player", origin=True)
    add("satellite.unmute", "unmute", "media_player", origin=True)
    add("media.seek_forward", "seek", "media_player", value_slot="seek_time", relative=1)
    add("media.seek_backward", "seek", "media_player", value_slot="seek_time", relative=-1)
    add("media.play_generic media.spotify_play media.play_playlist media.play_artist media.play_on_destination satellite.media_here", "music", "media_player", dependency="Music Assistant", value_slot="media_query")
    add("media.whole_home", "music", "media_player", dependency="Music Assistant explicitly configured player group", value_slot="media_query", plural=True)
    add("media.transfer media.transfer_destination", "transfer", "media_player", dependency="Music Assistant transfer_queue", value_slot="media_destination")
    add("media.now_playing", "query", "media_player", metric="media")
    add("cover.open gate.open", "open", "cover", sensitive=True)
    # Ordinary curtains are not access devices; executor derives sensitivity from device_class.
    add("cover.close gate.close", "close", "cover")
    add("cover.position_set cover.position_percent", "position", "cover", value_slot="position")
    add("cover.status gate.status", "query", "cover")
    add("lock.lock", "lock", "lock")
    add("lock.unlock", "unlock", "lock", sensitive=True)
    add("lock.status", "query", "lock")
    add("door.status window.status", "query", "binary_sensor", metric="opening")
    add("security.openings_any_open", "query", "binary_sensor cover", metric="opening", aggregate="any", plural=True)
    add("camera.show", "camera_view", "camera", dependency="explicit display adapter")
    add("alarm.arm", "arm", "alarm_control_panel")
    add("alarm.disarm", "disarm", "alarm_control_panel", sensitive=True)
    add("alarm.status", "query", "alarm_control_panel")
    add("energy.device_query", "query", "sensor", metric="power", device_class="power", target_slot="device")
    add("energy.home_query", "query", "sensor", metric="power", device_class="power", aggregate="sum", plural=True, dependency="explicit non-overlapping energy sources")
    add("battery.device_query", "query", "sensor", metric="battery", device_class="battery", target_slot="device")
    add("battery.low_query", "query", "sensor", metric="battery", device_class="battery", aggregate="filter", plural=True, threshold=20)
    add("water.leak_query", "query", "binary_sensor", device_class="moisture", metric="leak", aggregate="any", plural=True)
    add("water.tank_level_query", "query", "sensor", metric="tank_level")
    add("appliance.status printer3d.status", "query", "sensor switch", target_slot="appliance")
    add("washer.remaining_time dishwasher.remaining_time", "query", "sensor", metric="remaining_time")
    add("vacuum.start", "vacuum_start", "vacuum")
    add("vacuum.stop", "vacuum_stop", "vacuum")
    add("vacuum.dock", "vacuum_dock", "vacuum")
    add("vacuum.clean_area", "vacuum_area", "vacuum", value_slot="area")
    add("mower.start", "mower_start", "lawn_mower")
    add("mower.pause", "mower_pause", "lawn_mower")
    add("mower.dock", "mower_dock", "lawn_mower")
    add("mower.status", "query", "lawn_mower")
    add("scene.activate", "activate", "scene", target_slot="scene")
    add("routine.activate", "activate", "script", target_slot="routine")
    add("home.all_off", "turn_off", "light switch fan media_player climate humidifier", plural=True)
    add("home.status_summary area.devices_query", "query", metric="inventory", plural=True)
    add("home.any_device_on", "query", aggregate="any", plural=True, state_filter="on")
    add("entity.where_query", "query", metric="area", target_slot="device")
    add("group.status", "query", "light", aggregate="filter", plural=True, state_filter="on")
    add("timer.set timer.start", "timer_start", "timer", value_slot="duration")
    add("timer.cancel timer.cancel_all", "timer_cancel", "timer")
    result["timer.cancel_all"]["plural"] = True
    add("timer.query", "query", "timer", metric="remaining")
    add("timer.pause", "timer_pause", "timer")
    add("timer.resume", "timer_resume", "timer")
    add("timer.finish", "timer_finish", "timer")
    add("timer.add_time", "timer_change", "timer", value_slot="delta_duration", relative=1)
    add("timer.remove_time", "timer_change", "timer", value_slot="delta_duration", relative=-1)
    add("time.current", "local_time")
    add("date.current", "local_date")
    add("weather.current", "query", "weather", metric="weather")
    add("weather.forecast", "forecast", "weather", value_slot="date")
    add("sunrise.query", "query", "sun", metric="sunrise")
    add("sunset.query", "query", "sun", metric="sunset")
    add("calendar.today_query calendar.date_query", "calendar", "calendar", value_slot="date_ref")
    add("calendar.next_event_query", "query", "calendar", metric="next_event")
    add("calendar.event_time_query", "calendar", "calendar", value_slot="event_title")
    add("calendar.add_event", "calendar_create", "calendar", dependency="explicit start and end datetime required")
    # The inspected HA building block does not register a delete_event service.
    add("calendar.remove_event", "binding", dependency="explicit calendar deletion adapter with UID")
    for intent in ["device.phone_find", "alarm.set", "alarm.cancel", "clock_alarm.query", "clock_alarm.cancel_all", "clock_alarm.snooze", "clock_alarm.dismiss", "announce.area", "call.contact", "printer3d.pause", "printer3d.resume"]:
        add(intent, "binding", dependency="explicit registered script/button adapter; no generic HA service")
    for intent, operation in {"cancel": "cancel", "repeat": "repeat_response", "confirm": "confirm", "deny": "cancel"}.items():
        add(f"assistant.{intent}", operation)
    add("satellite.light_here_on", "turn_on", "light", origin=True, plural=True)
    add("satellite.light_here_off", "turn_off", "light", origin=True, plural=True)
    add("satellite.device_here_off", "turn_off", context_target=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stt-root", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    source = args.stt_root / "arandu_stt/datasets/commands/arandu_comandos_comuns_ptbr_v2.csv"
    license_text = (args.stt_root / "LICENSE").read_text(encoding="utf-8")
    if not license_text.startswith("MIT License"):
        raise SystemExit("Upstream license changed: review before importing")
    with source.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file, strict=True))
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["intent"], []).append(row)
    mapping = dispositions()
    missing = set(grouped) - set(mapping)
    if missing:
        raise SystemExit(f"Unreviewed intents: {sorted(missing)}")
    inventory = []
    for intent, examples in sorted(grouped.items()):
        spec = mapping[intent]
        inventory.append({
            "intent_id": intent, "family": examples[0]["dominio"],
            "phrases": sorted({row["frase_exemplo"] for row in examples}),
            "templates": sorted({row["frase_template"] for row in examples}),
            "expected_parameters": sorted({slot for row in examples for slot in row["slots_esperados"].split("|") if slot}),
            "semantic": spec,
            "adapter": "configured_binding" if spec["operation"] == "binding" else "home_assistant" if spec["domains"] else "local",
            "available_support": "conditional; see coverage.json for measured conformance and runtime dependencies",
            "external_dependency": spec.get("dependency"),
            "tests": ["tools/contextual-evaluate.py", "tests/mlp/test_contextual.py", "addon/engine/tests/contextual.rs"],
        })
    try:
        commit = subprocess.check_output(["git", "-C", str(args.stt_root), "rev-parse", "HEAD"], text=True).strip()
    except subprocess.CalledProcessError:
        commit = None
    provenance = {"source": "https://github.com/jaimevictor/Arandu-STT", "commit": commit,
                  "license": "MIT (repository license; dataset manifest says user_supplied)",
                  "sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "rows": len(rows),
                  "intents": len(inventory), "families": len({row['dominio'] for row in rows}),
                  "evaluation_kind": "internal corpus conformance; no generalization claim"}
    provenance["reference_inputs"] = [{"path": name, "sha256": hashlib.sha256((args.stt_root / name).read_bytes()).hexdigest()} for name in ["arandu_stt/datasets/commands/arandu_comandos_comuns_ptbr_v2.csv", "arandu_stt/datasets/household/arandu_household_entities_ptbr.csv", "arandu_stt/datasets/lexicon/arandu_lexico_generico_palavras_ptbr.csv", "arandu_stt/datasets/names/arandu_nomes_comuns_brasil.csv"]]
    grammar = [{"intent": item["intent_id"], "templates": item["templates"], **item["semantic"]} for item in inventory]
    outputs = {
        "data/contextual/stt-inventory.json": {"provenance": provenance, "intents": inventory},
        "addon/engine/data/grammar.json": grammar,
        "data/contextual/provenance.json": provenance,
    }
    for name, value in outputs.items():
        path = ROOT / name
        text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                raise SystemExit(f"Generated file differs: {name}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
    notice = ROOT / "addon/engine/data/STT-MIT.txt"
    if not args.check:
        notice.write_text(license_text, encoding="utf-8", newline="\n")
    print(f"STT inventory: {len(rows)} phrases, {len(inventory)} reviewed intents")


if __name__ == "__main__":
    main()
