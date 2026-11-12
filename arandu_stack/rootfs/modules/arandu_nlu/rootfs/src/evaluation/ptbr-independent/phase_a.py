"""Phase A candidate corpus and oracle, separate from frozen v1 inputs."""
from __future__ import annotations

from typing import Any

from semantic import CatalogIndex, compare_case, validate_case, validate_response


PROVENANCE = {
    "kind": "PROJECT_AUTHORED_SYNTHETIC",
    "license": "Apache-2.0",
    "annotation": "manual Phase A contract label before product baseline",
    "source": "Arandu MLP requirements",
    "copied_text": False,
}


def plan(action: str, target: str, percentage: int | None = None) -> dict[str, Any]:
    operation: dict[str, Any] = {"action": action, "targets": [target]}
    if percentage is not None:
        operation["percentage"] = percentage
    return {"status": "plan", "version": 1, "operations": [operation]}


def candidate(identifier: str, text: str, expected: dict[str, Any], **dimensions: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": identifier,
        "split": "phase-a-candidate",
        "catalog_id": "catalog-v1",
        "text": text,
        "bucket": "expected-pass-now" if expected["status"] == "plan" else "expected-reject-now",
        "expected": expected,
        "dimensions": dimensions,
        "provenance": dict(PROVENANCE),
    }


def cases() -> list[dict[str, Any]]:
    return [
        candidate("a-alias-plural-light", "Acenda as luzes da sala.", plan("turn_on", "reg_light_sala"), action="turn_on", area="sala", targeting="plural-controlled"),
        candidate("a-area-bedroom-alias", "Ligue a luz do dormitório.", plan("turn_on", "reg_light_quarto"), action="turn_on", area="quarto-alias", targeting="area"),
        candidate("a-query-alias", "Como está a luz principal do escritório?", plan("get_state", "reg_light_escritorio"), action="get_state", targeting="alias"),
        candidate("a-fan-boundary", "Deixe o ventilador da sala em 100 por cento.", plan("set_fan_percentage", "reg_fan_sala", 100), action="set_fan_percentage", percentage="upper-boundary", targeting="alias"),
        candidate("a-ambiguous-alias", "Acenda o abajur.", {"status": "ambiguous", "version": 1}, action="turn_on", targeting="ambiguous"),
        candidate("a-incompatible-domain", "Ajuste a temperatura da sala para 40 por cento.", {"status": "no_match", "version": 1}, action="set_fan_percentage", domain="sensor", targeting="incompatible"),
    ]


def validate_cases(items: list[dict[str, Any]], catalog: CatalogIndex) -> None:
    seen: set[str] = set()
    for item in items:
        if item["id"] in seen:
            raise ValueError(f"duplicate Phase A case: {item['id']}")
        validate_case(item, "catalog-v1", catalog)
        seen.add(item["id"])


def compare(item: dict[str, Any], actual: Any, catalog: CatalogIndex):
    return compare_case(validate_response(item["expected"], catalog), actual, catalog)
