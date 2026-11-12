"""Independent protocol validation and semantic comparison for PT-BR evaluation."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

PROTOCOL_VERSION = 1
MAX_OPERATIONS = 4
MAX_TARGETS_PER_OPERATION = 32
VALID_ACTIONS = frozenset({"get_state", "set_fan_percentage", "turn_off", "turn_on"})
VALID_STATUSES = frozenset({"ambiguous", "invalid_request", "no_match", "plan"})
VALID_BUCKETS = frozenset({"expected-pass-now", "expected-reject-now", "known-gap-future"})
VALID_DOMAINS = frozenset({"binary_sensor", "fan", "light", "sensor", "switch"})
EFFECT_ACTIONS = frozenset({"set_fan_percentage", "turn_off", "turn_on"})
READ_ACTION = "get_state"


def action_supports_domain(action: str, domain: str) -> bool:
    if action == READ_ACTION:
        return domain in VALID_DOMAINS
    if action == "set_fan_percentage":
        return domain == "fan"
    return domain in {"fan", "light", "switch"}


class ValidationError(ValueError):
    """Raised when evaluation data or a product response violates protocol."""


def _reject_constant(value: str) -> None:
    raise ValidationError(f"JSON constant is not permitted: {value}")


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(text: str) -> Any:
    """Decode strict JSON without duplicate keys or non-finite constants."""
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicates, parse_constant=_reject_constant)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValidationError(f"invalid JSON: {error}") from error


def _object(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"{name} must be an object")
    return value


def _array(value: Any, name: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise ValidationError(f"{name} must be an array")
    return value


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{name} must be a non-empty string")
    return value


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{name} must be an integer")
    return value


def _fields(value: Mapping[str, Any], name: str, required: set[str], optional: set[str] = set()) -> None:
    keys = set(value)
    missing = required - keys
    unknown = keys - required - optional
    if missing:
        raise ValidationError(f"{name} is missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise ValidationError(f"{name} has unknown fields: {', '.join(sorted(unknown))}")


def _version(value: Any, name: str) -> None:
    if _integer(value, name) != PROTOCOL_VERSION:
        raise ValidationError(f"{name} must equal {PROTOCOL_VERSION}")


@dataclass(frozen=True)
class CatalogIndex:
    action_targets: Mapping[str, frozenset[str]]
    all_targets: frozenset[str]


def validate_catalog(catalog: Any, *, require_primary: bool = False) -> CatalogIndex:
    value = _object(catalog, "catalog")
    _fields(value, "catalog", {"areas", "entities", "version"})
    _version(value["version"], "catalog.version")
    areas = _array(value["areas"], "catalog.areas")
    entities = _array(value["entities"], "catalog.entities")
    area_ids: set[str] = set()
    for index, area_raw in enumerate(areas):
        area = _object(area_raw, f"catalog.areas[{index}]")
        _fields(area, f"catalog.areas[{index}]", {"area_id", "names"})
        area_id = _string(area["area_id"], f"catalog.areas[{index}].area_id")
        if area_id in area_ids:
            raise ValidationError(f"duplicate area_id: {area_id}")
        area_ids.add(area_id)
        names = _array(area["names"], f"catalog.areas[{index}].names")
        if not names or len(names) > 8:
            raise ValidationError("area names must contain 1..8 values")
        for name_index, name in enumerate(names):
            _string(name, f"catalog.areas[{index}].names[{name_index}]")

    registry_ids: set[str] = set()
    action_targets: dict[str, set[str]] = {action: set() for action in VALID_ACTIONS}
    for index, entity_raw in enumerate(entities):
        entity = _object(entity_raw, f"catalog.entities[{index}]")
        required = {"actions", "area_id", "domain", "entity_id", "names", "registry_id"}
        optional = {"is_primary"}
        _fields(entity, f"catalog.entities[{index}]", required, optional)
        if require_primary and "is_primary" not in entity:
            raise ValidationError(f"catalog.entities[{index}] is missing fields: is_primary")
        registry_id = _string(entity["registry_id"], f"catalog.entities[{index}].registry_id")
        if "is_primary" in entity and not isinstance(entity["is_primary"], bool):
            raise ValidationError(f"catalog.entities[{index}].is_primary must be boolean")
        is_primary = entity.get("is_primary", False)
        if registry_id in registry_ids:
            raise ValidationError(f"duplicate registry_id: {registry_id}")
        registry_ids.add(registry_id)
        if entity["area_id"] is not None and _string(entity["area_id"], f"catalog.entities[{index}].area_id") not in area_ids:
            raise ValidationError(f"unknown area_id for {registry_id}")
        domain = _string(entity["domain"], f"catalog.entities[{index}].domain")
        if domain not in VALID_DOMAINS:
            raise ValidationError(f"unknown entity domain: {domain}")
        entity_id = _string(entity["entity_id"], f"catalog.entities[{index}].entity_id")
        if not entity_id.startswith(f"{domain}."):
            raise ValidationError(f"entity_id domain does not match entity domain for {registry_id}")
        names = _array(entity["names"], f"catalog.entities[{index}].names")
        if not names or len(names) > 8:
            raise ValidationError("entity names must contain 1..8 values")
        for name_index, name in enumerate(names):
            _string(name, f"catalog.entities[{index}].names[{name_index}]")
        actions = _array(entity["actions"], f"catalog.entities[{index}].actions")
        if not actions or len(actions) > 4:
            raise ValidationError("entity actions must contain 1..4 values")
        seen_actions: set[str] = set()
        for action in actions:
            if action not in VALID_ACTIONS:
                raise ValidationError(f"unknown catalog action: {action!r}")
            if action in seen_actions:
                raise ValidationError(f"duplicate catalog action for {registry_id}: {action}")
            if not action_supports_domain(action, domain):
                raise ValidationError(f"catalog action {action} is unsupported for {domain}")
            seen_actions.add(action)
            action_targets[action].add(registry_id)

    return CatalogIndex(
        action_targets={action: frozenset(targets) for action, targets in action_targets.items()},
        all_targets=frozenset(registry_ids),
    )


def validate_operation(raw: Any, catalog: CatalogIndex, name: str) -> dict[str, Any]:
    value = _object(raw, name)
    _fields(value, name, {"action", "targets"}, {"percentage"})
    action = value["action"]
    if action not in VALID_ACTIONS:
        raise ValidationError(f"{name}.action is invalid")
    targets = _array(value["targets"], f"{name}.targets")
    if not targets or len(targets) > MAX_TARGETS_PER_OPERATION:
        raise ValidationError(f"{name}.targets must contain 1..{MAX_TARGETS_PER_OPERATION} values")
    target_values = [_string(target, f"{name}.targets[{index}]") for index, target in enumerate(targets)]
    if len(set(target_values)) != len(target_values):
        raise ValidationError(f"{name}.targets contains duplicates")
    unsupported = set(target_values) - catalog.action_targets[action]
    if unsupported:
        raise ValidationError(f"{name}.targets do not support {action}: {', '.join(sorted(unsupported))}")
    has_percentage = "percentage" in value
    if action == "set_fan_percentage":
        if not has_percentage:
            raise ValidationError(f"{name}.percentage is required for set_fan_percentage")
        percentage = _integer(value["percentage"], f"{name}.percentage")
        if not 0 <= percentage <= 100:
            raise ValidationError(f"{name}.percentage must be between 0 and 100")
    elif has_percentage:
        raise ValidationError(f"{name}.percentage is only valid for set_fan_percentage")
    else:
        percentage = None
    return {"action": action, "targets": tuple(sorted(target_values)), "percentage": percentage}


def validate_response(raw: Any, catalog: CatalogIndex) -> dict[str, Any]:
    value = _object(raw, "response")
    _fields(value, "response", {"status", "version"}, {"operations"})
    _version(value["version"], "response.version")
    status = value["status"]
    if status not in VALID_STATUSES:
        raise ValidationError("response.status is invalid")
    if status != "plan":
        if "operations" in value:
            raise ValidationError("non-plan response must not include operations")
        return {"status": status, "version": PROTOCOL_VERSION}
    if "operations" not in value:
        raise ValidationError("plan response must include operations")
    operations = _array(value["operations"], "response.operations")
    if not operations or len(operations) > MAX_OPERATIONS:
        raise ValidationError(f"response.operations must contain 1..{MAX_OPERATIONS} values")
    normalized = [validate_operation(operation, catalog, f"response.operations[{index}]") for index, operation in enumerate(operations)]
    actions = {operation["action"] for operation in normalized}
    if READ_ACTION in actions and len(actions) > 1:
        raise ValidationError("plan cannot mix get_state with effect actions")
    if actions & EFFECT_ACTIONS:
        affected: set[str] = set()
        for operation in normalized:
            if operation["action"] in EFFECT_ACTIONS:
                overlap = affected & set(operation["targets"])
                if overlap:
                    raise ValidationError(f"effect target reused across operations: {', '.join(sorted(overlap))}")
                affected.update(operation["targets"])
    return {"status": "plan", "version": PROTOCOL_VERSION, "operations": tuple(normalized)}


def validate_case(raw: Any, catalog_id: str, catalog: CatalogIndex) -> dict[str, Any]:
    value = _object(raw, "case")
    _fields(
        value,
        "case",
        {"schema_version", "id", "split", "catalog_id", "text", "bucket", "expected", "dimensions", "provenance"},
    )
    _version(value["schema_version"], "case.schema_version")
    _string(value["id"], "case.id")
    _string(value["split"], "case.split")
    if value["catalog_id"] != catalog_id:
        raise ValidationError("case.catalog_id does not match catalog")
    if not isinstance(value["text"], str):
        raise ValidationError("case.text must be a string")
    if value["bucket"] not in VALID_BUCKETS:
        raise ValidationError("case.bucket is invalid")
    expected = validate_response(value["expected"], catalog)
    dimensions = _object(value["dimensions"], "case.dimensions")
    if not dimensions:
        raise ValidationError("case.dimensions must not be empty")
    provenance = _object(value["provenance"], "case.provenance")
    _fields(provenance, "case.provenance", {"kind", "license", "annotation", "source", "copied_text"})
    for key in ("kind", "license", "annotation", "source"):
        _string(provenance[key], f"case.provenance.{key}")
    if provenance["copied_text"] is not False:
        raise ValidationError("case.provenance.copied_text must be false")
    return {"id": value["id"], "bucket": value["bucket"], "expected": expected}


@dataclass(frozen=True)
class Comparison:
    classification: str
    detail: str


def compare_case(expected: Mapping[str, Any], actual_raw: Any, catalog: CatalogIndex) -> Comparison:
    """Validate actual response, then compare semantics with frozen expectation."""
    try:
        actual = validate_response(actual_raw, catalog)
        expected_normalized = expected if isinstance(expected.get("operations"), tuple) else validate_response(expected, catalog)
    except ValidationError as error:
        return Comparison("protocol_error", str(error))
    if expected_normalized["status"] == "plan":
        if actual["status"] != "plan":
            return Comparison("unexpected_rejection", f"expected plan, got {actual['status']}")
        if expected_normalized == actual:
            return Comparison("exact_pass", "semantic plan matches")
        return Comparison("semantic_mismatch", "validated plan differs from expected plan")
    if actual["status"] == "plan":
        return Comparison("unsafe_acceptance", f"expected {expected_normalized['status']}, got plan")
    if expected_normalized == actual:
        return Comparison("exact_pass", "rejection status matches")
    return Comparison("semantic_mismatch", f"expected {expected_normalized['status']}, got {actual['status']}")
