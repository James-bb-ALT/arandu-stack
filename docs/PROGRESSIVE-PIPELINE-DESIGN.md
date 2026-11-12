# Arandu Decision VNext — Progressive Resolution Pipeline Design

## Architecture Overview

Every user request passes through an additive **v5 Progressive Resolution Pipeline** while preserving full v4 wire compatibility (`/v4/catalog`, `/v4/interpret`):

1. **NLU v5 (`POST /v5/interpret`):**
   - Deterministically parses and resolves full commands (`request_kind="resolved_plan"`) with **0 Decision / 0 LLM** calls.
   - Enforces safety refusals (`request_kind="safety_refusal"`: negation, continuous conditions, quoted commands) before any model sees the request.
   - When a command is incomplete or has an ASR typo (e.g., `"lingua a tv"`), emits a `PartialUnderstanding` freezing known slots (`target`, `area`, `parameters`, `constraints.excluded_targets`) and marking only unresolved slots (`unresolved: ["action"]`) along with `allowed_actions` from the entity's real HA capabilities.

2. **Decision VNext Gap-Filler (`action="partial"` / `decision_partial_v1`):**
   - Performs zero-weight deterministic triage (`possible_action`, `ha_query`, `general_conversation`, `denied`, `unknown`).
   - Repairs **only** unresolved slots using PT-BR phonetic/lexical normalization (`phonetics_ptbr.py`) + optional embeddings over `allowed_actions` only.
   - Never alters frozen `resolved`, `conflicting`, or `forbidden` slots; sets `confidence: null`, `calibration_valid: false`, `requires_confirmation: true` for uncalibrated ASR repairs.

3. **Local Typed Home Graph (`custom_components/local_nlu/home_graph.py`):**
   - Built deterministically from the authorized HA catalog (`contextual_catalog.py`), never Graphify.
   - Enforces per-user `allowed_entity_ids` / Assist exposure and generation-keyed cache invalidation (`HomeGraphCache`).
   - Bounded read-only retrieval (`search_entities`, `get_entity`, `get_area`, `get_capabilities`, `get_related`, `get_state`, `get_graph_summary`) capped at `max_rounds=2`.

4. **LLM Handoff (`arandu_llm/structured.py`):**
   - Receives `PartialUnderstanding`, Decision evidence, and authorized `graph_context` (serialized strictly as data).
   - Emits explicit `mode`: `conversation_response`, `repair_proposal`, `read_query`, `clarification`, or `refusal`.

5. **Deterministic NLU Review (`POST /v5/review` + `progressive_contract.py::validate_repair_proposal`):**
   - Any `Decision` or `LLM` repair proposal MUST return to `review_proposal` to verify slot immutability, constraint preservation (`negated`, `excluded_targets`, `conditional`, `quoted_content`), catalog membership, and real target capabilities before `ContextualRuntime.execute`.

## Decision RAM Residency (`RESIDENT` / `LAZY` / `OFF`)

- `RESIDENT` (default): preloads weights at startup if installed; reports real `pid`, `rss_bytes`, `is_model_loaded`, `cold_start_ms`, `last_infer_ms`, `cache_status`, and `resolved_slots_count`.
- `LAZY`: defers weight loading until the first inference request and keeps them warm afterward.
- `OFF`: never loads model weights; deterministic PT-BR phonetic triage and gap-filling remain available without claiming loaded weights.
