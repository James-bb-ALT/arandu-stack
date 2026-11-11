"""Phase B corpus and catalog helpers."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "data" / "phase-b"
CATALOG_PATH = DATA / "catalog-v1.json"
CORPUS_PATH = DATA / "corpus-v1.jsonl"
SPEC_PATH = DATA / "spec-v1.json"
PROVENANCE = {
    "kind": "PROJECT_AUTHORED_SYNTHETIC",
    "license": "Apache-2.0",
    "annotation": "manual Phase B contract label before product baseline",
    "source": "Arandu Phase B contract",
    "copied_text": False,
}


def plan(action: str, targets: list[str], percentage: int | None = None) -> dict[str, Any]:
    operation: dict[str, Any] = {"action": action, "targets": targets}
    if percentage is not None:
        operation["percentage"] = percentage
    return {"status": "plan", "version": 1, "operations": [operation]}


def case(identifier: str, text: str, expected: dict[str, Any], bucket: str, **dimensions: str) -> dict[str, Any]:
    return {"schema_version": 1, "id": identifier, "split": "phase-b-candidate", "catalog_id": "phase-b-catalog-v1", "text": text, "bucket": bucket, "expected": expected, "dimensions": dimensions, "provenance": dict(PROVENANCE)}


def cases() -> list[dict[str, Any]]:
    return [
        case("b-single", "Ligue a luz da sala.", plan("turn_on", ["reg_light_sala"]), "expected-pass-now", composition="single", kind="valid"),
        {**case("b-single-query", "Qual é o estado da cafeteira?", plan("get_state", ["reg_switch_cafeteira"]), "expected-pass-now", composition="single-query", kind="valid")},
        {**case("b-ordered-2", "Apague a luz da sala e ligue a luz do quarto.", {"status":"plan","version":1,"operations":[{"action":"turn_off","targets":["reg_light_sala"]},{"action":"turn_on","targets":["reg_light_quarto"]}]}, "expected-pass-now", composition="ordered-2", kind="valid")},
        {**case("b-ordered-2-reversed", "Ligue a luz do quarto e apague a luz da sala.", {"status":"plan","version":1,"operations":[{"action":"turn_on","targets":["reg_light_quarto"]},{"action":"turn_off","targets":["reg_light_sala"]}]}, "expected-pass-now", composition="ordered-2-reversed", kind="valid")},
        {**case("b-ordered-3", "Ligue a cafeteira e coloque o ventilador em 40 por cento e apague a luz da sala.", {"status":"plan","version":1,"operations":[{"action":"turn_on","targets":["reg_switch_cafeteira"]},{"action":"set_fan_percentage","percentage":40,"targets":["reg_fan_quarto"]},{"action":"turn_off","targets":["reg_light_sala"]}]}, "expected-pass-now", composition="ordered-3", kind="valid")},
        {**case("b-ordered-3-reversed", "Apague a luz da sala e ligue a luz do quarto e ligue a cafeteira.", {"status":"plan","version":1,"operations":[{"action":"turn_off","targets":["reg_light_sala"]},{"action":"turn_on","targets":["reg_light_quarto"]},{"action":"turn_on","targets":["reg_switch_cafeteira"]}]}, "expected-pass-now", composition="ordered-3-reversed", kind="valid")},
        {**case("b-ordered-4", "Ligue a luz da sala e desligue a luz do quarto e ligue a cafeteira e coloque o ventilador em 50 por cento.", {"status":"plan","version":1,"operations":[{"action":"turn_on","targets":["reg_light_sala"]},{"action":"turn_off","targets":["reg_light_quarto"]},{"action":"turn_on","targets":["reg_switch_cafeteira"]},{"action":"set_fan_percentage","percentage":50,"targets":["reg_fan_quarto"]}]}, "expected-pass-now", composition="ordered-4", kind="valid")},
        {**case("b-ordered-4-reversed", "Coloque o ventilador em 60 por cento e ligue a cafeteira e apague a luz da sala e ligue a luz do quarto.", {"status":"plan","version":1,"operations":[{"action":"set_fan_percentage","percentage":60,"targets":["reg_fan_quarto"]},{"action":"turn_on","targets":["reg_switch_cafeteira"]},{"action":"turn_off","targets":["reg_light_sala"]},{"action":"turn_on","targets":["reg_light_quarto"]}]}, "expected-pass-now", composition="ordered-4-reversed", kind="valid")},
        {**case("b-target-conjunction", "Ligue a luz da sala e do quarto.", plan("turn_on", ["reg_light_quarto", "reg_light_sala"]), "expected-pass-now", composition="target-conjunction", kind="valid")},
        {**case("b-four-target-clauses", "Ligue as luzes da sala, do quarto, da cozinha e da sala.", {"status":"no_match","version":1}, "expected-reject-now", composition="target-clauses", kind="limit")},
        {**case("b-normalization", "LIGUE A LUZ DO DORMITÓRIO.", plan("turn_on", ["reg_light_quarto"]), "expected-pass-now", composition="normalization", kind="valid")},
        {**case("b-five-operations", "Ligue a luz da sala e desligue a luz do quarto e ligue a cafeteira e apague o ventilador e ligue a luz da sala.", {"status":"no_match","version":1}, "expected-reject-now", composition="over-limit", kind="atomic")},
        {**case("b-fail-position-1", "Ligue o projetor e ligue a luz da sala.", {"status":"no_match","version":1}, "expected-reject-now", composition="failure-position-1", kind="atomic")},
        {**case("b-fail-position-2", "Ligue a luz da sala e ligue o projetor e ligue a cafeteira.", {"status":"no_match","version":1}, "expected-reject-now", composition="failure-position-2", kind="atomic")},
        {**case("b-fail-position-3", "Ligue a luz da sala e ligue a cafeteira e ligue o projetor e apague a luz do quarto.", {"status":"no_match","version":1}, "expected-reject-now", composition="failure-position-3", kind="atomic")},
        {**case("b-fail-position-4", "Ligue a luz da sala e ligue a cafeteira e apague a luz do quarto e ligue o projetor.", {"status":"no_match","version":1}, "expected-reject-now", composition="failure-position-4", kind="atomic")},
        {**case("b-ambiguous-position-2", "Ligue a luz da sala e ligue o abajur.", {"status":"ambiguous","version":1}, "expected-reject-now", composition="ambiguity-position-2", kind="atomic")},
        {**case("b-ambiguous-position-3", "Ligue a luz da sala e ligue a cafeteira e ligue o abajur.", {"status":"ambiguous","version":1}, "expected-reject-now", composition="ambiguity-position-3", kind="atomic")},
        {**case("b-duplicate-target", "Ligue a luz da sala e desligue a luz da sala.", {"status":"no_match","version":1}, "expected-reject-now", composition="duplicate-target", kind="atomic")},
        {**case("b-duplicate-target-late", "Ligue a luz da sala e ligue a cafeteira e desligue a luz da sala.", {"status":"no_match","version":1}, "expected-reject-now", composition="duplicate-target-late", kind="atomic")},
        {**case("b-query-effect", "Qual é o estado da cafeteira e desligue a cafeteira.", {"status":"no_match","version":1}, "expected-reject-now", composition="query-effect", kind="atomic")},
        {**case("b-late-invalid-percentage", "Ligue a luz da sala e coloque o ventilador em 101 por cento.", {"status":"no_match","version":1}, "expected-reject-now", composition="invalid-percentage", kind="atomic")},
        {**case("b-invalid-percentage-first", "Coloque o ventilador em 101 por cento e ligue a luz da sala.", {"status":"no_match","version":1}, "expected-reject-now", composition="invalid-percentage-position-1", kind="atomic")},
    ]


def write_corpus(path: Path = CORPUS_PATH) -> None:
    path.write_text("".join(json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for item in cases()), encoding="utf-8", newline="\n")
