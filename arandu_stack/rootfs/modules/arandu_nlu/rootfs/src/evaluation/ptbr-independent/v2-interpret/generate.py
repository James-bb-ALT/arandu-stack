"""Generate deterministic v2-interpretation corpus (labels hand-authored).

Each row asserts a complete v2 plan outcome (plan with ordered operations,
ambiguous, or no_match) for one utterance against snapshot-v1.json. Labels
are written before any v2 implementation and never derived from product
output. Ellipsis-with-inheritance rows assert principled abstention: the
area-grammar bridging they need does not exist in ER snapshots.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "evaluation" / "ptbr-independent" / "v2-interpret"
SNAPSHOT = json.loads((BASE / "snapshot-v1.json").read_text(encoding="utf-8"))

P = {"kind": "PROJECT_AUTHORED_SYNTHETIC", "license": "Apache-2.0",
     "annotation": "manual v2-interpretation contract label before product implementation",
     "source": "ADR-0052 Step 6 activation plan", "copied_text": False}

REGISTRY = {e["registry_id"] for e in SNAPSHOT["entities"]}


def op(action, targets, percentage=None):
    value = {"action": action, "targets": sorted(targets)}
    if percentage is not None:
        value["percentage"] = percentage
    return value


def row(identifier, text, status, operations=None, generation="gen-001", dimensions=()):
    for operation in operations or []:
        for target in operation["targets"]:
            if target not in REGISTRY:
                raise SystemExit(f"unknown registry target: {identifier} {target}")
    expected: dict = {"status": status}
    if operations is not None:
        expected["operations"] = operations
    value = {"schema_version": 1, "id": identifier, "split": "v2-interpret-dev",
             "snapshot_id": SNAPSHOT["catalog_id"], "generation": generation,
             "text": text, "expected": expected,
             "dimensions": dict(dimensions), "provenance": P}
    return value


def cases():
    D = lambda *pairs: tuple(pairs)
    T = "turn_on"
    F = "turn_off"
    return [
        row("v2-single-alias", "Apague o abajur da sala.", "plan", [op(F, ["reg_lamp"])],
            dimensions=D(("kind", "positive"), ("action", "turn_off"), ("match", "exact"), ("ambiguity", "none"), ("operation", "single"), ("evidence", "alias"))),
        row("v2-single-id", "Acenda light.luz_sala.", "plan", [op(T, ["reg_main"])],
            dimensions=D(("kind", "positive"), ("action", "turn_on"), ("match", "exact"), ("ambiguity", "none"), ("operation", "single"), ("evidence", "external"))),
        row("v2-multi-target", "Apague o abajur da sala e o abajur do quarto.", "plan", [op(F, ["reg_lamp", "reg_qlamp"])],
            dimensions=D(("kind", "positive"), ("action", "turn_off"), ("match", "exact"), ("ambiguity", "none"), ("operation", "single"), ("evidence", "alias"))),
        row("v2-mixed-ops", "Acenda a luz da sala e desligue o ventilador do quarto.", "plan", [op(T, ["reg_main"]), op(F, ["reg_fan"])],
            dimensions=D(("kind", "positive"), ("action", "multi"), ("match", "exact"), ("ambiguity", "none"), ("operation", "multi"), ("evidence", "alias"))),
        row("v2-fan-percent", "Coloque o ventilador em 50 por cento.", "plan", [op("set_fan_percentage", ["reg_fan"], 50)],
            dimensions=D(("kind", "positive"), ("action", "fan"), ("match", "exact"), ("ambiguity", "none"), ("operation", "single"), ("evidence", "display"))),
        row("v2-late-ambiguity", "Apague o abajur da sala e o abajur.", "ambiguous",
            dimensions=D(("kind", "negative"), ("action", "turn_off"), ("match", "exact"), ("ambiguity", "late"), ("operation", "single"), ("evidence", "display"))),
        row("v2-conflict-reuse", "Apague o abajur da sala e ligue o abajur da sala.", "no_match",
            dimensions=D(("kind", "negative"), ("action", "multi"), ("match", "exact"), ("ambiguity", "none"), ("operation", "reject"), ("evidence", "alias"))),
        row("v2-last-op-failure", "Acenda a luz da sala e desligue o projetor.", "no_match",
            dimensions=D(("kind", "negative"), ("action", "multi"), ("match", "unknown"), ("ambiguity", "none"), ("operation", "reject"), ("evidence", "none"))),
        row("v2-unlinked-alias", "Acenda o abajur da copa.", "no_match",
            dimensions=D(("kind", "negative"), ("action", "turn_on"), ("match", "unlinked"), ("ambiguity", "none"), ("operation", "reject"), ("evidence", "unlinked"))),
        row("v2-unlinked-id", "Acenda light.luz_sala da copa.", "no_match",
            dimensions=D(("kind", "negative"), ("action", "turn_on"), ("match", "unlinked"), ("ambiguity", "none"), ("operation", "reject"), ("evidence", "unlinked"))),
        row("v2-unlinked-late", "Acenda a luz da sala e do banheiro.", "no_match",
            dimensions=D(("kind", "negative"), ("action", "turn_on"), ("match", "unlinked"), ("ambiguity", "none"), ("operation", "reject"), ("evidence", "unlinked"))),
        row("v2-ellipsis-abstain", "Acenda a luz da sala e do quarto.", "no_match",
            dimensions=D(("kind", "negative"), ("action", "turn_on"), ("match", "exact"), ("ambiguity", "none"), ("operation", "reject"), ("evidence", "inherited"))),
        row("v2-stale", "Apague o abajur da sala.", "no_match", generation="gen-000",
            dimensions=D(("kind", "negative"), ("action", "turn_off"), ("match", "stale"), ("ambiguity", "none"), ("operation", "reject"), ("evidence", "none"))),
        row("v2-5segments", "Acenda a luz e apague o abajur e ligue o ventilador e desligue a luz e acenda o abajur.", "no_match",
            dimensions=D(("kind", "negative"), ("action", "multi"), ("match", "exact"), ("ambiguity", "none"), ("operation", "reject"), ("evidence", "none"))),
        row("v2-query", "Qual é o estado do abajur?", "no_match",
            dimensions=D(("kind", "negative"), ("action", "none"), ("match", "exact"), ("ambiguity", "none"), ("operation", "reject"), ("evidence", "none"))),
        row("v2-empty", "", "no_match",
            dimensions=D(("kind", "negative"), ("action", "none"), ("match", "malformed"), ("ambiguity", "none"), ("operation", "reject"), ("evidence", "none"))),
        row("v2-domain-compat", "Acenda a temperatura.", "no_match",
            dimensions=D(("kind", "negative"), ("action", "turn_on"), ("match", "exact"), ("ambiguity", "none"), ("operation", "reject"), ("evidence", "display"))),
        row("v2-utf8", "ACENDA O ABAJUR DO QUARTO.", "plan", [op(T, ["reg_qlamp"])],
            dimensions=D(("kind", "positive"), ("action", "turn_on"), ("match", "exact"), ("ambiguity", "none"), ("operation", "single"), ("evidence", "alias"))),
        row("v2-alias-precedence", "Acenda a luz principal.", "plan", [op(T, ["reg_main"])],
            dimensions=D(("kind", "positive"), ("action", "turn_on"), ("match", "exact"), ("ambiguity", "none"), ("operation", "single"), ("evidence", "alias"))),
        row("v2-ambiguous-display", "Acenda o abajur.", "ambiguous",
            dimensions=D(("kind", "negative"), ("action", "turn_on"), ("match", "exact"), ("ambiguity", "tie"), ("operation", "single"), ("evidence", "display"))),
        row("v2-id-beats", "Acenda light.abajur_quarto.", "plan", [op(T, ["reg_qlamp"])],
            dimensions=D(("kind", "positive"), ("action", "turn_on"), ("match", "exact"), ("ambiguity", "none"), ("operation", "single"), ("evidence", "external"))),
        row("v2-control", "Acenda\ta luz.", "no_match",
            dimensions=D(("kind", "negative"), ("action", "none"), ("match", "malformed"), ("ambiguity", "none"), ("operation", "reject"), ("evidence", "none"))),
        row("v2-too-long", "a" * 2049, "no_match",
            dimensions=D(("kind", "negative"), ("action", "none"), ("match", "malformed"), ("ambiguity", "none"), ("operation", "reject"), ("evidence", "none"))),
        row("v2-area-alias-fan", "Ligue o ventilador do quarto.", "plan", [op(T, ["reg_fan"])],
            dimensions=D(("kind", "positive"), ("action", "turn_on"), ("match", "exact"), ("ambiguity", "none"), ("operation", "single"), ("evidence", "alias"))),
    ]


def render(items):
    return "".join(json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for item in items)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = render(cases())
    corpus = BASE / "corpus-v1.jsonl"
    if args.check:
        if not corpus.exists() or corpus.read_text(encoding="utf-8") != expected:
            raise SystemExit("v2-interpret corpus is not reproducible")
        print("v2-interpret corpus verified")
    else:
        corpus.write_text(expected, encoding="utf-8", newline="\n")
        print(f"v2-interpret corpus generated: {corpus}")


if __name__ == "__main__":
    main()
