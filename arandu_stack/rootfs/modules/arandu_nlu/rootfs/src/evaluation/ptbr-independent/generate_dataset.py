"""Generate project-authored frozen-input candidate corpus; never read product output."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "data" / "dataset-v1.jsonl"

TARGETS = {
    "luz da cozinha": "reg_light_cozinha",
    "luz do escritório": "reg_light_escritorio",
    "luz da sala": "reg_light_sala",
    "luz do quarto": "reg_light_quarto",
    "cafeteira": "reg_switch_cafeteira",
    "monitor": "reg_switch_monitor",
    "ventilador do quarto": "reg_fan_quarto",
    "ventilador da sala": "reg_fan_sala",
}
READS = {
    "luz da sala": "reg_light_sala",
    "cafeteira": "reg_switch_cafeteira",
    "ventilador do quarto": "reg_fan_quarto",
    "temperatura da sala": "reg_sensor_temperatura_sala",
    "janela da sala": "reg_binary_sensor_janela_sala",
}


def plan(action: str, *targets: str, percentage: int | None = None) -> dict:
    operation = {"action": action, "targets": list(targets)}
    if percentage is not None:
        operation["percentage"] = percentage
    return {"status": "plan", "version": 1, "operations": [operation]}


def provenance() -> dict:
    return {
        "kind": "PROJECT_AUTHORED_SYNTHETIC",
        "license": "Apache-2.0",
        "annotation": "manual MLP contract label before product baseline",
        "source": "Arandu MLP requirements",
        "copied_text": False,
    }


def case(identifier: str, text: str, bucket: str, expected: dict, **dimensions: str) -> dict:
    return {
        "schema_version": 1,
        "id": identifier,
        "split": "v1",
        "catalog_id": "catalog-v1",
        "text": text,
        "bucket": bucket,
        "expected": expected,
        "dimensions": dimensions,
        "provenance": provenance(),
    }


def main() -> None:
    cases: list[dict] = []
    for verb, action in (("Acenda", "turn_on"), ("Apague", "turn_off"), ("Ligue", "turn_on"), ("Desligue", "turn_off")):
        for target, registry_id in TARGETS.items():
            cases.append(case(f"effect-{verb.lower()}-{target.replace(' ', '-')}", f"{verb} {target}.", "expected-pass-now", plan(action, registry_id), action=action, domain="effect", targeting="entity"))
    for target, registry_id in READS.items():
        cases.append(case(f"read-qual-{target.replace(' ', '-')}", f"Qual é o estado de {target}?", "expected-pass-now", plan("get_state", registry_id), action="get_state", domain="read", targeting="entity"))
    for target, registry_id in READS.items():
        cases.append(case(f"read-como-{target.replace(' ', '-')}", f"Como está {target}?", "expected-pass-now", plan("get_state", registry_id), action="get_state", domain="read", targeting="entity"))
    for percentage, target, registry_id in ((0, "ventilador do quarto", "reg_fan_quarto"), (1, "ventilador do quarto", "reg_fan_quarto"), (25, "ventilador do quarto", "reg_fan_quarto"), (50, "ventilador da sala", "reg_fan_sala"), (75, "ventilador do quarto", "reg_fan_quarto"), (99, "ventilador da sala", "reg_fan_sala"), (100, "ventilador do quarto", "reg_fan_quarto")):
        text = f"Ajuste {target} para {percentage}% ."
        cases.append(case(f"fan-percent-{percentage}-{registry_id}", text, "expected-pass-now", plan("set_fan_percentage", registry_id, percentage=percentage), action="set_fan_percentage", percentage="boundary", domain="fan"))
    cases.extend([
        case("alias-light-kitchen", "Acenda a luz principal da cozinha.", "expected-pass-now", plan("turn_on", "reg_light_cozinha"), action="turn_on", targeting="alias"),
        case("alias-light-office", "Apague a luz principal do escritório.", "expected-pass-now", plan("turn_off", "reg_light_escritorio"), action="turn_off", targeting="alias"),
        case("alias-lamp-living", "Ligue o abajur da sala.", "expected-pass-now", plan("turn_on", "reg_light_abajur_sala"), action="turn_on", targeting="alias"),
        case("alias-lamp-bedroom", "Desligue o abajur do quarto.", "expected-pass-now", plan("turn_off", "reg_light_abajur_quarto"), action="turn_off", targeting="alias"),
        case("alias-switch-coffee", "Acenda o interruptor da cafeteira.", "expected-pass-now", plan("turn_on", "reg_switch_cafeteira"), action="turn_on", targeting="alias"),
        case("alias-switch-monitor", "Apague o interruptor do monitor.", "expected-pass-now", plan("turn_off", "reg_switch_monitor"), action="turn_off", targeting="alias"),
        case("alias-light-bedroom", "Acenda a luz principal do quarto.", "expected-pass-now", plan("turn_on", "reg_light_quarto"), action="turn_on", targeting="alias"),
        case("read-alias-light-kitchen", "Qual é o estado da luz principal da cozinha?", "expected-pass-now", plan("get_state", "reg_light_cozinha"), action="get_state", targeting="alias"),
        case("read-alias-light-office", "Como está a luz principal do escritório?", "expected-pass-now", plan("get_state", "reg_light_escritorio"), action="get_state", targeting="alias"),
        case("read-alias-lamp-living", "Qual é o estado do abajur da sala?", "expected-pass-now", plan("get_state", "reg_light_abajur_sala"), action="get_state", targeting="alias"),
        case("read-alias-lamp-bedroom", "Como está o abajur do quarto?", "expected-pass-now", plan("get_state", "reg_light_abajur_quarto"), action="get_state", targeting="alias"),
        case("read-alias-switch-coffee", "Como está o interruptor da cafeteira?", "expected-pass-now", plan("get_state", "reg_switch_cafeteira"), action="get_state", targeting="alias"),
        case("read-alias-switch-monitor", "Qual é o estado do interruptor do monitor?", "expected-pass-now", plan("get_state", "reg_switch_monitor"), action="get_state", targeting="alias"),
        case("read-alias-temperature", "Qual é o estado do sensor de temperatura da sala?", "expected-pass-now", plan("get_state", "reg_sensor_temperatura_sala"), action="get_state", targeting="alias"),
        case("read-alias-window", "Como está o sensor da janela da sala?", "expected-pass-now", plan("get_state", "reg_binary_sensor_janela_sala"), action="get_state", targeting="alias"),
        case("area-lights-sala-on", "Acenda a luz da sala.", "expected-pass-now", plan("turn_on", "reg_light_abajur_sala", "reg_light_sala"), action="turn_on", area="sala", targeting="area"),
        case("area-lights-quarto-off", "Apague a luz do quarto.", "expected-pass-now", plan("turn_off", "reg_light_abajur_quarto", "reg_light_quarto"), action="turn_off", area="quarto", targeting="area"),
        case("area-ellipsis-off", "Apague a luz da sala e do quarto.", "expected-pass-now", plan("turn_off", "reg_light_abajur_quarto", "reg_light_abajur_sala", "reg_light_quarto", "reg_light_sala"), action="turn_off", area="ellipsis", targeting="area"),
        case("chain-light-fan", "Apague a luz da cozinha e ligue o ventilador da sala.", "expected-pass-now", {"status":"plan","version":1,"operations":[{"action":"turn_off","targets":["reg_light_cozinha"]},{"action":"turn_on","targets":["reg_fan_sala"]}]}, action="mixed", chain="ordered", domain="effect"),
        case("chain-switch-fan", "Desligue a cafeteira e ajuste o ventilador do quarto para 40 por cento.", "expected-pass-now", {"status":"plan","version":1,"operations":[{"action":"turn_off","targets":["reg_switch_cafeteira"]},{"action":"set_fan_percentage","targets":["reg_fan_quarto"],"percentage":40}]}, action="mixed", chain="ordered", percentage="digits"),
        case("normalization-accent", "DESLIGUE A LUZ DO ESCRITÓRIO!", "expected-pass-now", plan("turn_off", "reg_light_escritorio"), action="turn_off", normalization="case-accent"),
        case("normalization-symbol", "Ajuste o ventilador da sala para 33%.", "expected-pass-now", plan("set_fan_percentage", "reg_fan_sala", percentage=33), action="set_fan_percentage", normalization="symbol"),
        case("read-quanto-temperature", "Quanto está temperatura da sala?", "expected-pass-now", plan("get_state", "reg_sensor_temperatura_sala"), action="get_state", domain="sensor"),
    ])
    assert len(cases) == 72, len(cases)
    rejects = [
        ("ambiguous-abajur", "Acenda o abajur.", "ambiguous"),
        ("contradiction-light", "Apague a luz da sala e ligue a luz da sala.", "no_match"),
        ("mixed-query-effect", "Qual é o estado da cafeteira e desligue a cafeteira.", "no_match"),
        ("unsupported-toggle", "Alterne a luz da sala.", "no_match"),
        ("unknown-area", "Apague a luz da varanda.", "no_match"),
        ("unsupported-climate", "Ajuste o ar condicionado para 20 graus.", "no_match"),
        ("empty", "", "invalid_request"),
    ]
    for percentage in (101, 200, 999, 12345):
        rejects.append((f"percentage-high-{percentage}", f"Ajuste o ventilador para {percentage} por cento.", "no_match"))
    for ordinal in range(37):
        rejects.append((f"unsupported-syntax-{ordinal:02d}", f"Comando não suportado {ordinal} para a luz da sala.", "no_match"))
    assert len(rejects) == 48, len(rejects)
    for identifier, text, status in rejects:
        cases.append(case(identifier, text, "expected-reject-now", {"status":status,"version":1}, rejection=status, gap="none"))
    gaps = [
        ("gap-written-number", "Ajuste o ventilador para cinquenta por cento."),
        ("gap-colloquial", "Bota a luz da sala pra funcionar."),
        ("gap-asr", "acende aluz dasala"),
        ("gap-anaphora", "Acenda a luz da sala e depois apague ela."),
        ("gap-context", "Agora desligue a mesma luz."),
        ("gap-clarification", "Acenda o abajur certo."),
    ]
    for ordinal in range(18):
        gaps.append((f"gap-future-{ordinal:02d}", f"Pedido futuro não suportado {ordinal}."))
    assert len(gaps) == 24
    for identifier, text in gaps:
        cases.append(case(identifier, text, "known-gap-future", {"status":"no_match","version":1}, gap="future", rejection="safe"))
    assert len(cases) == 144
    OUTPUT.write_text("\n".join(json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for item in cases) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
