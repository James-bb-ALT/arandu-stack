# Arandu Progressive Pipeline — Verification & Test Report

## Test Suites Added

### Rust (`arandu_nlu`)
- `tests/test_progressive_partial.rs`: Verifies `interpret_partial` without verb (`"lingua a tv do quarto"`), safety refusal preservation (`"não liga a tv"`, continuous condition), resolved plan compatibility with v4, and exclusion preservation (`"lingua o quarto menos o ar"`).
- `tests/test_progressive_review.rs`: Verifies `review_proposal` accepts valid action repair with `requires_confirmation=true`, rejects overwriting frozen `target`, rejects unsupported action, and rejects safety-locked partials.

### Python
- `tests/test_decision_partial_repair.py`: PT-BR phonetics (`"lingua"` -> `"liga"`), `reason_partial` uncalibrated `confidence=None`, conflicting targets clarification, `general_conversation` routing, and safety/negation blocking.
- `tests/test_home_graph.py`: Per-user exposure/permission filtering, bounded queries, generation-keyed cache invalidation, and `build_graph_context` round cap.
- `tests/test_llm_graph_handoff.py`: `context_for` enrichment with `partial_understanding`, `decision_evidence`, `graph_context`, and safety-locked refusal without calling `llama.cpp`.
- `tests/test_progressive_orchestrator.py`: E2E orchestrator flow for NLU direct (0 Decision/LLM), `"lingua a tv"` repair + NLU review + confirmation, and `"conta uma piada"` conversation (0 HA effects).
- `tests/test_progressive_security.py`: Slot immutability, exclusion enforcement, unauthorized target rejection, safety-lock, and Decision-offline LLM fallback rejection.
- `tests/test_decision_residency.py`: Real `aiohttp` service tests for `OFF`, `LAZY`, and `RESIDENT` policies and honest `/status` readiness telemetry.

## Hardware & Environment Scope
- All Rust (`cargo fmt --check`, `cargo clippy --all-targets -- -D warnings`, `cargo test`) and Python (`pytest`) suites were executed in the Windows development worktree.
- Physical HAOS / Intel i5-6500T / OpenVINO iGPU runtime latency and live audio playback were **not** executed in this environment and require on-device verification.
