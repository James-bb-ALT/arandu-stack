# PT-BR Semantic Equivalence & Ontology Coverage (V3)

- **Source of truth:** `arandu_stack/shared/semantics/pt_BR/concepts.json`
- **Generator & CI check:** `scripts/generate_semantics.py` (`--check`)
- **Conflict report:** `arandu_stack/shared/semantics/pt_BR/conflict_report.json`

## Coverage Summary

- **260** canonical PT-BR concepts across actions, entities/domains, areas, queries, quantities, constraints, and ASR patterns
- **1,620** indexed surface forms
- **42** tracked lexical ambiguities (e.g. `ar` -> `climate | air_quality_sensor | fan | air_purifier`)
- **31** explicit non-equivalences enforced by generator checks (`sensor de temperatura` != `termostato`, `lista de compras` != `lista de tarefas`, etc.)
- **17** ASR confusion families & **20** morphological normalization rules

## Generated Targets

- `custom_components/local_nlu/generated_semantics_ptbr.py` (+ `arandu_nlu` mirror)
- `arandu_decision/generated_semantics_ptbr.py`
- `arandu_llm/generated_semantics_ptbr.py`
- `arandu_stt/generated_semantics_ptbr.py`
- `arandu_nlu/.../contextual/generated_semantics_ptbr.rs`

## STT Dynamic Authorized Lexicon

- `arandu_stt/config/lexical.json` disables closed-vocabulary coercion (`"closed_vocabulary": false`) so unknown words are preserved rather than forced into a closed list, while authorized Home Assistant names/aliases and shared ASR variants guide recognition.
