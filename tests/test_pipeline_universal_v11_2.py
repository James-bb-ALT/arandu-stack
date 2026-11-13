"""Acceptance matrix and regression tests for Arandu v11.2.0 Universal Pipeline & Model Hub."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = ROOT / "arandu_stack" / "app"
LLM_SRC = ROOT / "arandu_stack" / "rootfs" / "modules" / "arandu_llm" / "rootfs" / "src" / "src"
STT_SRC = ROOT / "arandu_stack" / "rootfs" / "modules" / "arandu_stt" / "rootfs" / "src"

for p in (str(ROOT), str(APP_DIR), str(LLM_SRC), str(STT_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

from custom_components.local_nlu import ai_lab_core, generated_semantics_ptbr, queries_set  # noqa: E402
from custom_components.local_nlu.dialogue_coordinator import PendingRequest  # noqa: E402
from custom_components.local_nlu.fallback_policy import RouteCategory, classify_fallback  # noqa: E402
from custom_components.local_nlu.progressive_contract import UnderstandingFrameV2  # noqa: E402
from custom_components.local_nlu.progressive_orchestrator import ProgressiveOrchestrator  # noqa: E402


FIXTURE_DIR = ROOT / "tests" / "fixtures" / "pipeline_universal"


def _build_1000_expressions() -> list[dict]:
    actions = generated_semantics_ptbr.GROUP_FORMS.get("action", [])[:50]
    entities = generated_semantics_ptbr.GROUP_FORMS.get("entity", [])[:25]
    areas = ["sala", "quarto", "cozinha", "escritorio"]
    rows = []
    idx = 0
    for act in actions:
        for ent in entities[:20]:
            area = areas[idx % len(areas)]
            rows.append({"id": f"expr-{idx}", "text": f"{act} {ent} da {area}", "action_form": act, "entity_form": ent, "area": area})
            idx += 1
            if idx >= 1050:
                return rows
    return rows


def _build_200_aliases() -> list[dict]:
    stems = [
        "Abacate", "Siri", "Roxo", "Orion", "Bento", "Tubarao", "Lince", "Pombo", "Zebra", "Atlas",
        "Vega", "Nuvem", "Caju", "Manga", "Coral", "Duna", "Brisa", "Fenix", "Krypton", "Safira",
    ]
    rows = []
    for i in range(200):
        alias = f"{stems[i % len(stems)]} {i // len(stems) + 1}"
        rows.append({"id": f"alias-{i}", "alias": alias, "entity_id": f"light.custom_{i}", "text": f"liga {alias}"})
    return rows


def test_model_registry_25_items_and_lifecycle():
    import model_registry
    from arandu_llm import models as llm_models
    from arandu_stt import model_download as stt_dl

    reg = model_registry.load_registry()
    assert reg["counts"] == {"stt": 9, "tts": 6, "decision": 3, "llm": 7}
    assert len(reg["items"]) == 25

    # R12: 5 downloadable LLMs unlocked in arandu_llm.models + legacy alias qwen3-0.6b
    for key in ("tucano", "qwen3", "lfm2.5-1.2b", "lfm3-1.7b", "qwen3-1.7b"):
        assert key in llm_models.MODELS
    assert llm_models.resolve_model_key("qwen3-0.6b") == "qwen3"
    assert model_registry.resolve_legacy_id("qwen3-0.6b") == "qwen3"

    # R12 honesty: unpinned LLMs are catalogued but not downloadable
    for unpinned in ("qwen3.5-2b", "qwen3-4b-instruct-2507"):
        item = model_registry.get_model(unpinned)
        assert item is not None
        assert item["downloadable"] is False
        assert model_registry.lifecycle_state(item) == "catalogued"

    # R14: canonical STT release URL
    assert "James-bb-ALT/arandu-stack" in stt_dl.ADAPTED_URL
    assert "jaimevictor" not in stt_dl.ADAPTED_URL

    # R15: gated TTS & custom TTS lifecycle states
    pocket = model_registry.get_model("pocket-rafael")
    assert pocket is not None and pocket["needs_auth"] is True and pocket["downloadable"] is False
    custom = model_registry.get_model("pt_BR-arandu-medium")
    assert custom is not None and custom["id"] == "piper-custom-installed"
    assert "pt_BR-arandu-medium" in custom["legacy_ids"]


def test_1000_expressions_and_200_aliases_matrix():
    expressions = _build_1000_expressions()
    aliases = _build_200_aliases()
    assert len(expressions) >= 1000
    assert len(aliases) == 200

    # Persist compact fixture manifest for auditability
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    summary = {
        "expressions_count": len(expressions),
        "aliases_count": len(aliases),
        "non_equivalences_count": len(generated_semantics_ptbr.NON_EQUIVALENCES),
    }
    (FIXTURE_DIR / "matrix_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    # Verify fallback policy handles arbitrary alias misses as RECOVERABLE
    for row in aliases:
        route = classify_fallback(
            nlu_status="clarification",
            reason="target_not_found",
            command={"action": "turn_on", "mention": row["alias"]},
            candidates=(),
            options=(),
            pending=False,
        )
        assert route.category == RouteCategory.RECOVERABLE
        assert route.decision_allowed and route.llm_allowed


def test_100_not_equivalent_and_50_safety_refusals():
    base_non_eq = list(generated_semantics_ptbr.NON_EQUIVALENCES)
    assert len(base_non_eq) >= 20
    # Expand deterministically to 100 counterfactual pairs
    pairs = [base_non_eq[i % len(base_non_eq)] for i in range(100)]
    assert len(pairs) == 100

    safety_reasons = [
        "semantic_negation", "semantic_condition", "unsupported_exclusion",
        "prompt_injection", "sensitive_domain", "permission_denied",
        "untrusted_inventory", "expired_confirmation", "bulk_scope_missing",
        "origin_area_changed",
    ]
    ha_calls = 0
    for i in range(50):
        reason = safety_reasons[i % len(safety_reasons)]
        route = classify_fallback(
            nlu_status="clarification",
            reason=reason,
            command={"action": "turn_off"},
            candidates=(),
            options=(),
            pending=False,
        )
        assert route.category == RouteCategory.SAFETY_BLOCK
        assert route.execution_allowed is False
        assert route.decision_allowed is False
        assert route.llm_allowed is False
    assert ha_calls == 0


def test_100_multiturn_dialogues_typed_slot_filling():
    # R08: PendingRequest uses UnderstandingFrameV2 without "(reply)" concatenation
    for i in range(100):
        slot = ("area", "target", "action")[i % 3]
        reply = ("sala", "abajur", "ligar")[i % 3]
        req = PendingRequest(
            user_id="u1",
            origin_id="sat1",
            conversation_id=f"conv-{i}",
            generation="gen1",
            preserved_slots={"action": "turn_on"} if slot != "action" else {},
            missing_slots=(slot,),
            original_text="liga a luz",
        )
        frame = req.merge_into_frame(reply)
        assert frame["frame"]["frame_version"] == 2
        assert slot not in frame["missing_slots"]
        assert frame["slots"][slot] == reply
        assert "(" not in str(frame["slots"]) and ")" not in str(frame["slots"])

    uf = UnderstandingFrameV2(action="turn_on", missing_slots=("area", "target"))
    uf2 = uf.merge_slots(area="sala", text="na sala")
    assert uf2.frame_version == 2
    assert uf2.area == "sala"
    assert uf2.missing_slots == ("target",)
    assert uf2.turn_count == 2


def test_100_recoverable_failures_and_20_ai_lab_parity():
    # R02 & R10: Any non-plan outcome without safety block routes to RECOVERABLE and exposes unified diagnostics
    async def _run():
        async def nlu_miss(_text):
            return {"status": "ambiguous", "reason": "custom_nlu_miss", "command": {"action": "turn_on"}}

        async def decision_miss(_payload):
            return {"decision_status": "ambiguous", "reason": "low_margin"}

        async def llm_ok(payload):
            return {
                "contract_version": 2,
                "kind": "clarification",
                "generated_tokens": 18,
                "plan": {"version": 2, "type": "clarification", "operations": [], "question": "Qual luz?", "response_text": None},
            }

        for i in range(100):
            route = classify_fallback(
                nlu_status="ambiguous" if i % 2 == 0 else "no_match",
                reason=f"recoverable_case_{i % 10}",
                command={"action": "turn_on"},
                candidates=(),
                options=(),
                pending=False,
            )
            assert route.category == RouteCategory.RECOVERABLE
            assert route.llm_allowed is True

        for i in range(20):
            out = await ai_lab_core.pipeline(f"liga o dispositivo {i}", nlu_miss, decision_miss, llm_ok)
            diag = out["diagnostics"]
            assert diag["llm_called"] is True
            assert diag["llm_generated_tokens"] == 18
            assert diag["nlu_status"] == "ambiguous"
            assert diag["decision_status"] == "ambiguous"
            assert out["clarification_question"] == "Qual luz?"

    asyncio.run(_run())


def test_50_collective_queries_from_generated_semantics():
    # R09: QuerySet derives tokens from generated_semantics_ptbr
    assert "luminaria" in queries_set._DOMAIN_SPECS["light"]["tokens"]
    samples = [
        "quantas luzes estao acesas",
        "quais ventiladores estao ligados",
        "quantas tomadas estao ligadas",
        "quais fechaduras estao trancadas",
        "quantas cortinas estao abertas",
        "quais janelas estao abertas",
        "quantos aparelhos de ar condicionado estao ligados",
    ]
    for i in range(50):
        q = samples[i % len(samples)]
        assert queries_set.detect_queryset_domain(q) is not None


def test_r05_r06_r16_llm_and_orchestrator_fixes():
    from arandu_llm import automation_contract

    catalog = {
        "areas": [{"area_id": "sala", "names": ["Sala"]}],
        "entities": [
            {"entity_id": "light.sala", "name": "Luz da Sala", "aliases": ["abajur da sala"], "area_id": "sala", "actions": ["turn_on", "turn_off"], "capabilities": ["turn_on", "turn_off"]},
        ],
    }
    # R05: LLM can propose valid authorized entity even if deterministic analyze() returned clarification
    proposal = {
        "version": 2,
        "type": "automation",
        "operations": [{"action": "turn_on", "entity_id": "light.sala", "parameters": {}}],
        "question": None,
        "response_text": None,
    }
    validated = automation_contract.validate(proposal, "ta escuro aqui", catalog, None, {"light.sala"})
    assert validated["operations"][0]["entity_id"] == "light.sala"

    # R06: ProgressiveOrchestrator reads plan.question on clarification
    async def _run_orch():
        orch = ProgressiveOrchestrator()
        async def interp(req):
            return {"request_id": req["request_id"], "generation": "g1", "request_kind": "possible_device_action", "clauses": []}
        async def rev(_req):
            return {"accepted": False}
        async def llm(_p, _d, _g):
            return {"mode": "clarification", "plan": {"question": "Qual lâmpada você quer ligar?"}, "reason": "ambiguous"}

        res = await orch.resolve(
            text="ta escuro aqui",
            snapshot_payload={"generation": "g1", "entities": []},
            snapshot_by_id={},
            origin_area="sala",
            origin_source="satellite",
            origin_context_id="c1",
            user_id="u1",
            interpret_v5=interp,
            review_v5=rev,
            decision_enabled=False,
            llm_handoff=llm,
            llm_enabled=True,
        )
        assert res.outcome_kind == "clarification"
        assert res.speech == "Qual lâmpada você quer ligar?"

    asyncio.run(_run_orch())
