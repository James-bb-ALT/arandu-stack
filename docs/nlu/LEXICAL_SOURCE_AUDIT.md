# Lexical Source & Fallback Audit (V3 Baseline)

**Baseline commit:** `8fc2c2fa16905e0112f2e8961d3a9cbcc9da65f0` (`v11.1.3`)  
**Branch:** `fix/arandu-universal-semantics-fallback-v3`

## 1. Hardcoded Lexical & Semantic Sources Inventory

| Layer / File | Kind of Hardcoded Knowledge | Lines / Symbols | Action in V3 |
| --- | --- | --- | --- |
| `custom_components/local_nlu/semantic_policy.json` (+ copies in `arandu_decision`, `arandu_llm`) | Negation, exclusion, condition, speech, effects, queries | L1-10 | Keep synchronized from shared PT-BR semantics generator (`arandu_stack/shared/semantics/pt_BR/concepts.json`) |
| `custom_components/local_nlu/semantic_context.py` (+ copies in `arandu_decision`, `arandu_llm`) | `ACTIONS` verb sets, `STOP`, `read_only_question`, `semantic_refusal` | L75-96 | Consume generated PT-BR verb/constraint sets while preserving deterministic safety boundary |
| `custom_components/local_nlu/decision_context.py` (+ copy in `arandu_decision`) | `CATEGORIES` (`light`, `fan`, `tv`, `climate`, `cover`), `constructions`, `advanced` regex | L12-19, L80-99 | Feed from shared PT-BR ontology categories & constructions |
| `arandu_decision/.../structured.py` | `INTENTS` (`turn_on`, `turn_off`, `query`) descriptions | L15-19 | Enrich from shared PT-BR ontology |
| `arandu_decision/.../partial_reasoner.py` | Early `needs_clarification` on `target.status == "conflicting"` and single-slot action loop | L66-140 | Multi-slot repair (`action`, `target`, `area`, `parameters`); treat set queries distinctly from singular ambiguity; avoid hard stop before LLM |
| `custom_components/local_nlu/light_aggregation.py` | Light-only regex (`_LIGHT_QUERY_RE`) | L15-27 | Replace/generalize with typed capability-aware `QuerySet` engine (`queries_set.py` / `light_aggregation.py` facade) |
| `arandu_nlu/.../contextual/slots.rs` & `normalize.rs` | `normalize`, `strip_article`, `strip_preposition`, number/duration/qualitative parsers | `slots.rs` L5-188, `normalize.rs` L3-100 | Add reversible PT-BR normalizer + generated PT-BR ontology/phonetic/ASR indices |
| `arandu_nlu/.../contextual/progressive.rs` | `extract_excluded_targets`, `extract_area`, `extract_target_candidates` | L40-100+ | Preserve partial slots/constraints, integrate PT-BR equivalence/ambiguity awareness |
| `arandu_stt/.../config/lexical.json` & `resources/asr_confusions.json` | `"closed_vocabulary": true`, hardcoded device/action typo maps | `lexical.json` L3, `asr_confusions.json` L1-42 | Disable closed-vocabulary coercion for unknown words; load dynamic authorized HA names/aliases + shared ASR/phonetic rules |

## 2. Routing & Progressive Fallback Defects (F01–F15 Summary)

1. **`unavailable + target_not_found` dead-end (`fallback_policy.py`):** Only `clarification` handled `target_not_found`, and `candidates or options` blocked LLM (`CLARIFY`, `llm_allowed=False`).
2. **Early stops before LLM (`progressive_orchestrator.py` L160-168, `intelligence.py` L201-205, `ai_lab_core.py` L226-227):** `needs_clarification` and `true_entity_ambiguity` terminated the pipeline instead of escalating to LLM with Decision evidence.
3. **Candidate cage in `progressive_contract.py` (L283-285):** `target_not_in_candidates` rejected valid authorized targets proposed by LLM if not in NLU's initial candidate list.
4. **Production / AI Lab divergence (`contextual_runtime.py` L371/567-596 vs `ai_lab.py` L341/381):** Production was still calling `/v4/interpret` + `fallback()`, while `/v5/interpret`, `/v5/review`, and `ProgressiveOrchestrator` were decoupled.
5. **Single-profile LLM temperature (`arandu_llm/structured.py` L171-195):** Same low temperature used for both strict JSON automation and free-form conversation.
6. **No `PendingRequest` multi-turn slot-filling (`contextual_runtime.py`, `dialogue_coordinator.py`):** Clarification answers without repeating the full command failed to merge with preserved slots.
