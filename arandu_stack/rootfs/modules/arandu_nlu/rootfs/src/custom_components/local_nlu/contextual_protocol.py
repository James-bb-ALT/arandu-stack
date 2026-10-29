"""Strict v4 semantic wire contract. Text never selects an arbitrary HA service."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any
import unicodedata

from .capabilities import ACTIONS
from .protocol import ProtocolError


def normalize(value: str) -> str:
    return " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in unicodedata.normalize("NFD", value) if not unicodedata.combining(ch)).split())


def identifier(value: Any) -> bool:
    return type(value) is str and 1 <= len(value) <= 128 and all(ch.isascii() and (ch.isalnum() or ch in "_-") for ch in value)


def text(value: Any, limit: int = 256) -> bool:
    return type(value) is str and 0 < len(value.encode("utf-8")) <= limit and not any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


@dataclass(frozen=True)
class Operation:
    intent: str
    action: str
    targets: tuple[str, ...]
    parameters: dict
    depends_on: tuple[int, ...] = ()
    evidence: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {"intent": self.intent, "action": self.action, "targets": list(self.targets), "parameters": self.parameters, "evidence": list(self.evidence), "depends_on": list(self.depends_on)}


@dataclass(frozen=True)
class Outcome:
    status: str
    operations: tuple[Operation, ...] = ()
    candidates: tuple[str, ...] = ()
    command: dict | None = None
    reason: str | None = None
    timings: dict | None = None
    options: tuple[dict, ...] = ()


PARAMETERS = frozenset(("value", "relative", "metric", "aggregate", "state_filter", "threshold", "area", "date", "provider", "secondary_targets", "scope", "category"))
AGGREGATES = frozenset(("count", "any", "all", "none", "min", "max", "sum", "average", "compare", "filter"))
METRICS = frozenset(("temperature", "target_temperature", "device_temperature", "humidity", "co2", "illuminance", "noise", "air_quality", "presence", "location", "opening", "power", "energy", "battery", "leak", "tank_level", "people_count", "remaining_time", "media", "inventory", "area", "remaining", "weather", "sunrise", "sunset", "next_event", "position", "speed"))


def validate_parameters(value: Any) -> dict:
    if type(value) is not dict or not set(value) <= PARAMETERS:
        raise ProtocolError("parameters")
    for key, item in value.items():
        if key in ("relative", "threshold"):
            if type(item) not in (int, float) or not math.isfinite(item) or abs(item) > 604_800:
                raise ProtocolError(key)
        elif key == "aggregate":
            if item not in AGGREGATES:
                raise ProtocolError(key)
        elif key == "metric":
            if item not in METRICS:
                raise ProtocolError(key)
        elif key == "scope":
            if item not in ("floor_group", "bulk_area", "bulk_floor_group", "bulk_global", "device_location"):
                raise ProtocolError(key)
        elif key == "category":
            if item not in ("light", "switch", "fan", "climate", "media_player", "humidifier"):
                raise ProtocolError(key)
        elif key == "secondary_targets":
            if type(item) is not list or len(item) > 32 or any(not identifier(target) for target in item):
                raise ProtocolError(key)
        elif key == "value":
            if type(item) in (float, int):
                if not math.isfinite(item) or abs(item) > 1_000_000:
                    raise ProtocolError(key)
            elif type(item) is str:
                if not text(item):
                    raise ProtocolError(key)
            elif type(item) is dict:
                if not set(item) <= {"summary", "date", "time", "start", "end", "duration", "message", "contact", "person", "camera", "area"} or any((type(val) not in (int, float) or not math.isfinite(val) or not 0 < val <= 604800) if key == "duration" else not text(val) for key, val in item.items()):
                    raise ProtocolError(key)
            elif type(item) is not bool:
                raise ProtocolError(key)
        elif not text(item):
            raise ProtocolError(key)
    return dict(value)


def parse(value: Any) -> Outcome:
    allowed = {"version", "status", "operations", "candidates", "command", "reason", "intent", "generation", "timings", "options"}
    if type(value) is not dict or type(value.get("version")) is not int or value["version"] != 4 or not set(value) <= allowed:
        raise ProtocolError("v4_response")
    status = value.get("status")
    if status not in ("plan", "catalog_ready", "clarification", "unavailable", "invalid_request", "no_match", "stale", "cancel", "confirm", "repeat_response"):
        raise ProtocolError("status")
    operations = value.get("operations", [])
    if type(operations) is not list or len(operations) > 4 or (status == "plan" and not operations) or (status != "plan" and operations):
        raise ProtocolError("operations")
    parsed = []
    for number, operation in enumerate(operations):
        if type(operation) is not dict or set(operation) != {"intent", "action", "targets", "parameters", "evidence", "depends_on"}:
            raise ProtocolError("operation")
        action = operation["action"]
        if action not in ACTIONS or not text(operation["intent"], 128):
            raise ProtocolError("action")
        targets = operation["targets"]
        if type(targets) is not list or len(targets) > 32 or any(not identifier(target) for target in targets) or targets != sorted(set(targets)):
            raise ProtocolError("targets")
        if not targets and action not in ("local_time", "local_date", "binding", "camera_view"):
            raise ProtocolError("empty_targets")
        dependencies = operation["depends_on"]
        if type(dependencies) is not list or any(type(item) is not int or not 0 <= item < number for item in dependencies) or dependencies != sorted(set(dependencies)):
            raise ProtocolError("dependencies")
        evidence = operation["evidence"]
        if type(evidence) is not list or len(evidence) > 8 or any(not text(item, 64) for item in evidence):
            raise ProtocolError("evidence")
        parsed.append(Operation(operation["intent"], action, tuple(targets), validate_parameters(operation["parameters"]), tuple(dependencies), tuple(evidence)))
    candidates = value.get("candidates", [])
    if type(candidates) is not list or len(candidates) > 32 or any(not identifier(item) for item in candidates) or candidates != sorted(set(candidates)):
        raise ProtocolError("candidates")
    command = value.get("command")
    if command is not None:
        keys = {"intent", "action", "domains", "mention", "area", "device_class", "plural", "origin", "parameters"}
        if type(command) is not dict or set(command) != keys or command["action"] not in ACTIONS or not text(command["intent"], 128):
            raise ProtocolError("command")
        if type(command["domains"]) is not list or len(command["domains"]) > 32 or any(not identifier(item) for item in command["domains"]):
            raise ProtocolError("domains")
        for key in ("mention", "area", "device_class"):
            if command[key] is not None and not text(command[key]):
                raise ProtocolError(key)
        if type(command["plural"]) is not bool or type(command["origin"]) is not bool:
            raise ProtocolError("command_flags")
        validate_parameters(command["parameters"])
    if "reason" in value and not text(value["reason"], 128):
        raise ProtocolError("reason")
    timings = value.get("timings", {})
    if type(timings) is not dict or len(timings) > 16 or any(not text(key, 64) or type(item) not in (int, float) or not math.isfinite(item) or item < 0 for key, item in timings.items()):
        raise ProtocolError("timings")
    options = value.get("options", [])
    if type(options) is not list or len(options) > 2 or (options and status != "clarification"):
        raise ProtocolError("selection_options")
    for option in options:
        if type(option) is not dict or set(option) != {"key", "targets", "area", "evidence"} or option["key"] not in ("name", "area"):
            raise ProtocolError("selection_option")
        targets = option["targets"]
        if type(targets) is not list or not 1 <= len(targets) <= 32 or targets != sorted(set(targets)) or any(not identifier(target) for target in targets):
            raise ProtocolError("selection_targets")
        if option["area"] is not None and not identifier(option["area"]):
            raise ProtocolError("selection_area")
        if type(option["evidence"]) is not list or not 1 <= len(option["evidence"]) <= 8 or any(not text(item, 64) for item in option["evidence"]):
            raise ProtocolError("selection_evidence")
    if options and ({option["key"] for option in options} != {"name", "area"} or command is None):
        raise ProtocolError("selection_command")
    return Outcome(status, tuple(parsed), tuple(candidates), command, value.get("reason"), timings, tuple(options))
