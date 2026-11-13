# Progressive Fallback Flow (V3): NLU v5 -> Decision -> LLM + HomeGraph -> NLU Review

## Pipeline Stages

1. **Deterministic NLU (`async_interpret_v5` / `v4`):**
   - Resolves deterministic single/multi-clause commands directly (`nlu_direct`).
   - Enforces hard safety boundaries (`safety_refusal`: negation, exclusion, conditional, prompt injection, stale/denied).
   - Evaluates read-only aggregated `QuerySet` queries across authorized domains (`light`, `fan`, `switch`, `lock`, `cover`, `binary_sensor`, `climate`) via `queries_set.py` and `light_aggregation.py`.
2. **Recoverable Routing (`fallback_policy.py`):**
   - `unavailable + target_not_found`, `clarification + target_not_found` (with or without candidates), `ambiguous_target`, `missing_slot`, and typed `no_compatible_capability` route as `RECOVERABLE` (`decision_allowed=True`, `llm_allowed=True`).
   - `SAFETY_BLOCK` and `PENDING_DIALOGUE` remain strictly non-escalatable.
3. **Decision Partial Reasoner (`arandu_decision/partial_reasoner.py`):**
   - Performs multi-slot repair (`action`, `target`) and passes ranked alternatives as evidence (`decision_evidence`) instead of stopping early on `conflicting` targets.
4. **LLM Handoff + Expandable `HomeGraph` (`progressive_contract.py`, `home_graph.py`, `arandu_llm/structured.py`):**
   - Pure `build_llm_handoff(...)` packages frozen slots, constraints, `decision_evidence`, `graph_context`, and `PendingRequest` session state.
   - `expand_graph_context(...)` explores authorized neighborhood entities/areas within depth/limit bounds.
   - `arandu_llm/structured.py` separates `automation_profile` (strict JSON schema, `temperature <= 0.25`) from `conversation_profile` (`temperature` in `0.7..0.85`).
   - `validate_repair_proposal` and Rust `review_proposal` allow `llm_proposed` targets within the user's authorized catalog (never excluded targets, never frozen `resolved`/`forbidden` slots).
5. **Multi-Turn Continuity (`PendingRequest` / `PendingRequestStore`):**
   - Scoped strictly by `(user_id, origin_id, conversation_id, catalog_generation)` with bounded TTL (`90s`).
