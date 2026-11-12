"""Generate deterministic entity-resolution corpus."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CATALOG = ROOT / "evaluation" / "ptbr-independent" / "entity-resolution" / "catalog-v1.json"
CORPUS = ROOT / "evaluation" / "ptbr-independent" / "entity-resolution" / "corpus-v1.jsonl"

P = {"kind":"PROJECT_AUTHORED_SYNTHETIC","license":"Apache-2.0","annotation":"manual entity-resolution contract label before product implementation","source":"ADR-0051 entity-resolution preparation","copied_text":False}

def row(identifier, text, outcome, *, mention, evidence=None, candidates=None, area=None, domain=None, capability=None, generation="gen-001", span=None, catalog_variant=None, dimensions=()):
    expected = {"outcome": outcome}
    if evidence is not None: expected["evidence"] = evidence
    if candidates is not None: expected["candidates"] = candidates
    constraints = {k:v for k,v in (("area_id",area),("domain",domain),("capability",capability)) if v is not None}
    value = {"schema_version":1,"id":identifier,"split":"entity-resolution-dev","catalog_id":"entity-resolution-catalog-v1","generation":generation,"text":text,"mention":mention,"span":span,"constraints":constraints,"expected":expected,"dimensions":dict(dimensions),"provenance":P}
    if catalog_variant is not None:
        value["catalog_variant"] = catalog_variant
    return value

def cases():
    return [
        row("er-external-id", "Acenda light.luz_sala.", "resolved", mention="light.luz_sala", evidence="external_entity_id", candidates=["reg_light_sala_main"], dimensions=(('kind','positive'),('precedence','external'))),
        row("er-alias", "Acenda a luz principal da sala.", "resolved", mention="luz principal da sala", evidence="explicit_registry_alias", candidates=["reg_light_sala_main"], area="area_sala", dimensions=(('kind','positive'),('precedence','alias'))),
        row("er-display-with-area", "Acenda o abajur da sala.", "resolved", mention="abajur", evidence="display_name_with_constraint", candidates=["reg_light_sala_lamp"], area="area_sala", dimensions=(('kind','positive'),('constraint','area'))),
        row("er-display-with-domain", "Acenda o ventilador da sala.", "resolved", mention="ventilador da sala", evidence="display_name_with_constraint", candidates=["reg_fan_sala"], domain="fan", dimensions=(('kind','positive'),('constraint','domain'))),
        row("er-area-alias", "Acenda a luz da sala.", "resolved", mention="luz da sala", evidence="display_name_with_constraint", candidates=["reg_light_sala_main"], area="area_sala", domain="light", dimensions=(('kind','positive'),('area','exact'))),
        row("er-ambiguous-display", "Acenda o abajur.", "ambiguous", mention="abajur", candidates=["reg_light_quarto_lamp","reg_light_sala_lamp"], dimensions=(('kind','negative'),('ambiguity','display'))),
        row("er-ambiguous-domain", "Acenda o abajur.", "ambiguous", mention="abajur", candidates=["reg_light_quarto_lamp","reg_light_sala_lamp"], domain="light", dimensions=(('kind','negative'),('ambiguity','display'))),
        row("er-no-match-unknown", "Acenda o projetor.", "no_match", mention="projetor", dimensions=(('kind','negative'),('match','unknown'))),
        row("er-no-match-display-only", "Acenda a cafeteira.", "no_match", mention="cafeteira", dimensions=(('kind','negative'),('match','display-only'))),
        row("er-no-match-conflict", "Acenda o abajur da sala.", "no_match", mention="abajur da sala", area="area_quarto", dimensions=(('kind','negative'),('constraint','contradiction'))),
        row("er-stale-generation", "Acenda a luz da sala.", "no_match", mention="luz da sala", area="area_sala", generation="gen-000", dimensions=(('kind','negative'),('generation','stale'))),
        row("er-invalid-span", "Acenda a luz da sala.", "no_match", mention="luz da sala", area="area_sala", span=[0,999], dimensions=(('kind','negative'),('span','out-of-range'))),
        row("er-reversed-span", "Acenda a luz da sala.", "no_match", mention="luz da sala", area="area_sala", span=[15,5], dimensions=(('kind','negative'),('span','reversed'))),
        row("er-span-mismatch", "Acenda a luz da sala.", "no_match", mention="luz da sala", area="area_sala", span=[8,15], dimensions=(('kind','negative'),('span','text-mismatch'))),
        row("er-domain-conflict", "Acenda a luz da sala.", "no_match", mention="luz da sala", area="area_sala", domain="switch", dimensions=(('kind','negative'),('constraint','domain-conflict'))),
        row("er-alias-precedence", "Acenda o interruptor da cafeteira.", "resolved", mention="interruptor da cafeteira", evidence="explicit_registry_alias", candidates=["reg_switch_kitchen"], dimensions=(('kind','positive'),('precedence','alias'))),
        row("er-id-beats-alias", "Acenda light.luz_sala.", "resolved", mention="light.luz_sala", evidence="external_entity_id", candidates=["reg_light_sala_main"], dimensions=(('kind','positive'),('precedence','external'))),
        row("er-catalog-permutation", "Acenda o abajur do quarto.", "resolved", mention="abajur do quarto", evidence="explicit_registry_alias", candidates=["reg_light_quarto_lamp"], area="area_quarto", dimensions=(('kind','positive'),('determinism','permutation'))),
        row("er-capability-conflict", "Ajuste o abajur para 50 por cento.", "no_match", mention="abajur", area="area_sala", domain="light", capability="set_fan_percentage", dimensions=(('kind','negative'),('capability','unsupported'))),
        row("er-empty-mention", "Acenda a luz da sala.", "no_match", mention="", dimensions=(('kind','negative'),('malformed','empty-mention'))),
        row("er-duplicate-id-candidate", "Acenda a luz da sala.", "no_match", mention="luz da sala", area="area_sala", catalog_variant="duplicate_registry_id", dimensions=(('kind','negative'),('catalog','duplicate-id'))),
    ]

def render(items):
    return ''.join(json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(',',':'))+'\n' for item in items)

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--check',action='store_true'); args=parser.parse_args()
    expected=render(cases())
    if args.check:
        if not CORPUS.exists() or CORPUS.read_text(encoding='utf-8') != expected: raise SystemExit('entity-resolution corpus is not reproducible')
        print('entity-resolution corpus verified')
    else:
        CORPUS.write_text(expected, encoding='utf-8', newline='\n'); print(f'entity-resolution corpus generated: {CORPUS}')
if __name__=='__main__': main()
