"""End-to-end progressive orchestrator tests using real NLU v5 logic and Decision/LLM stubs."""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "arandu_stack/rootfs/modules/arandu_decision/rootfs/src/src"))

from arandu_decision.partial_reasoner import reason_partial
from custom_components.local_nlu.progressive_orchestrator import ProgressiveOrchestrator


def _snapshot():
    payload = {
        "version": 4,
        "generation": "a" * 64,
        "areas": [{"area_id": "quarto", "names": ["Quarto"]}],
        "entities": [
            {
                "registry_id": "tv_quarto",
                "entity_id": "media_player.tv_quarto",
                "domain": "media_player",
                "area_id": "quarto",
                "name": "Televisão",
                "aliases": ["tv"],
                "device_class": "tv",
                "actions": ["turn_on", "turn_off", "play", "pause"],
                "attributes": {"available": True},
            },
            {
                "registry_id": "ar_quarto",
                "entity_id": "climate.ar_quarto",
                "domain": "climate",
                "area_id": "quarto",
                "name": "Ar-condicionado",
                "aliases": ["ar"],
                "actions": ["turn_on", "turn_off"],
                "attributes": {"available": True},
            },
        ],
    }
    by_id = {e["registry_id"]: e for e in payload["entities"]}
    return payload, by_id


def test_orchestrator_nlu_direct_skips_decision_and_llm():
    payload, by_id = _snapshot()
    orch = ProgressiveOrchestrator()
    calls = {"decision": 0, "llm": 0}

    async def interpret_v5(req):
        return {
            "version": 5,
            "request_id": req["request_id"],
            "catalog_generation": req["generation"],
            "identity_scope_hash": req["identity_scope_hash"],
            "origin": {"area_id": "quarto", "source": "satellite", "context_id": None},
            "request_kind": "resolved_plan",
            "clauses": [],
            "safety_flags": [],
            "v4_outcome": {"status": "plan", "operations": [{"intent": "media_player.turn_on", "action": "turn_on", "targets": ["tv_quarto"], "parameters": {}}]},
        }

    async def review_v5(req):
        raise AssertionError("review_v5 must not run for resolved_plan")

    async def decision_cb(p):
        calls["decision"] += 1
        return {}

    async def llm_cb(p, d, g):
        calls["llm"] += 1
        return {}

    res = asyncio.run(
        orch.resolve(
            text="liga a tv do quarto",
            snapshot_payload=payload,
            snapshot_by_id=by_id,
            origin_area="quarto",
            origin_source="satellite",
            origin_context_id="ctx-1",
            user_id="user-1",
            interpret_v5=interpret_v5,
            review_v5=review_v5,
            decision_partial=decision_cb,
            llm_handoff=llm_cb,
        )
    )
    assert res.outcome_kind == "nlu_direct"
    assert calls == {"decision": 0, "llm": 0}


def test_orchestrator_lingua_a_tv_repairs_via_decision_and_requires_confirmation():
    payload, by_id = _snapshot()
    orch = ProgressiveOrchestrator()
    llm_calls = 0

    async def interpret_v5(req):
        return {
            "version": 5,
            "request_id": req["request_id"],
            "catalog_generation": req["generation"],
            "identity_scope_hash": req["identity_scope_hash"],
            "origin": {"area_id": "quarto", "source": "satellite", "context_id": None},
            "request_kind": "possible_device_action",
            "clauses": [
                {
                    "clause_id": 0,
                    "original_text": "lingua a tv",
                    "slots": {
                        "action": {"value": None, "status": "unknown", "evidence": []},
                        "target": {"value": "tv_quarto", "status": "resolved", "evidence": ["authorized_exact_alias:tv"], "source": "catalog_alias"},
                        "area": {"value": "quarto", "status": "resolved", "evidence": ["entity_area"], "source": "catalog_exact"},
                        "parameters": {"value": {}, "status": "resolved", "evidence": ["no_parameter_requested"], "source": "nlu_rule"},
                    },
                    "allowed_actions": ["turn_on", "turn_off", "play", "pause"],
                    "constraints": {"negated": False, "excluded_targets": [], "conditional": False, "quoted_content": False},
                    "unresolved": ["action"],
                }
            ],
            "safety_flags": [],
        }

    async def review_v5(req):
        assert req["repairs"][0]["value"] == "turn_on"
        return {
            "version": 5,
            "accepted": True,
            "status": "plan",
            "requires_confirmation": True,
            "operations": [{"intent": "progressive.turn_on", "action": "turn_on", "targets": ["tv_quarto"], "parameters": {}}],
        }

    async def decision_cb(p):
        return reason_partial(p)

    async def llm_cb(p, d, g):
        nonlocal llm_calls
        llm_calls += 1
        return {}

    res = asyncio.run(
        orch.resolve(
            text="lingua a tv",
            snapshot_payload=payload,
            snapshot_by_id=by_id,
            origin_area="quarto",
            origin_source="satellite",
            origin_context_id="ctx-1",
            user_id="user-1",
            interpret_v5=interpret_v5,
            review_v5=review_v5,
            decision_partial=decision_cb,
            llm_handoff=llm_cb,
        )
    )
    assert res.outcome_kind == "confirmation_required"
    assert res.operations[0]["action"] == "turn_on"
    assert "Televisão" in (res.speech or "")
    assert llm_calls == 0


def test_orchestrator_routes_joke_to_llm_conversation_zero_ha_effects():
    payload, by_id = _snapshot()
    orch = ProgressiveOrchestrator()

    async def interpret_v5(req):
        return {
            "version": 5,
            "request_id": req["request_id"],
            "catalog_generation": req["generation"],
            "identity_scope_hash": req["identity_scope_hash"],
            "origin": {"area_id": "quarto", "source": "satellite", "context_id": None},
            "request_kind": "unknown",
            "clauses": [
                {
                    "clause_id": 0,
                    "original_text": "conta uma piada sobre tv",
                    "slots": {"action": {"value": None, "status": "unknown", "evidence": []}},
                    "allowed_actions": [],
                    "constraints": {"negated": False, "excluded_targets": [], "conditional": False, "quoted_content": False},
                    "unresolved": ["action"],
                }
            ],
            "safety_flags": [],
        }

    async def review_v5(req):
        raise AssertionError("review_v5 must not run for conversation")

    async def decision_cb(p):
        return reason_partial(p)

    async def llm_cb(p, d, g):
        assert d["route"] == "general_conversation"
        assert g is None
        return {"mode": "conversation_response", "response_text": "Por que a TV foi ao médico? Porque estava sem sinal!"}

    res = asyncio.run(
        orch.resolve(
            text="conta uma piada sobre tv",
            snapshot_payload=payload,
            snapshot_by_id=by_id,
            origin_area="quarto",
            origin_source="satellite",
            origin_context_id="ctx-1",
            user_id="user-1",
            interpret_v5=interpret_v5,
            review_v5=review_v5,
            decision_partial=decision_cb,
            llm_handoff=llm_cb,
        )
    )
    assert res.outcome_kind == "conversation_response"
    assert res.operations == ()
    assert "sinal" in (res.speech or "")
