"""Security tests for progressive pipeline: immutability, exclusions, permissions, and stale catalog."""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from custom_components.local_nlu.progressive_contract import validate_repair_proposal
from custom_components.local_nlu.progressive_orchestrator import ProgressiveOrchestrator


def _partial(excluded=None, safety=None, negated=False):
    return {
        "version": 5,
        "request_id": "sec-1",
        "catalog_generation": "a" * 64,
        "identity_scope_hash": "b" * 64,
        "origin": {"area_id": "quarto", "source": "satellite", "context_id": "c1"},
        "request_kind": "safety_refusal" if safety else "possible_device_action",
        "clauses": [
            {
                "clause_id": 0,
                "original_text": "lingua a tv",
                "slots": {
                    "action": {"value": None, "status": "unknown", "evidence": []},
                    "target": {"value": "tv_quarto", "status": "resolved", "evidence": ["authorized_exact_alias:tv"], "source": "catalog_alias"},
                },
                "allowed_actions": ["turn_on", "turn_off"],
                "constraints": {
                    "negated": negated,
                    "excluded_targets": excluded or [],
                    "conditional": False,
                    "quoted_content": False,
                },
                "unresolved": ["action"],
            }
        ],
        "safety_flags": safety or [],
    }


def test_security_rejects_frozen_slot_override_excluded_target_and_unauthorized_target():
    ok, reason = validate_repair_proposal(
        _partial(),
        [{"clause_id": 0, "slot": "target", "value": "ar_quarto", "source": "llm_proposed", "evidence": []}],
        authorized_targets={"tv_quarto", "ar_quarto"},
    )
    assert not ok
    assert reason == "immutable_slot:target"

    p_unknown_target = _partial(excluded=["ar_quarto"])
    p_unknown_target["clauses"][0]["slots"]["target"] = {"value": None, "status": "unknown", "evidence": []}
    p_unknown_target["clauses"][0]["unresolved"].append("target")
    ok_excl, reason_excl = validate_repair_proposal(
        p_unknown_target,
        [{"clause_id": 0, "slot": "target", "value": "ar_quarto", "source": "llm_proposed", "evidence": []}],
        authorized_targets={"tv_quarto", "ar_quarto"},
    )
    assert not ok_excl
    assert reason_excl == "excluded_target_violation"

    ok_unauth, reason_unauth = validate_repair_proposal(
        p_unknown_target,
        [{"clause_id": 0, "slot": "target", "value": "lock_cofre", "source": "llm_proposed", "evidence": []}],
        authorized_targets={"tv_quarto"},
    )
    assert not ok_unauth
    assert reason_unauth == "unauthorized_target"


def test_security_safety_locked_and_decision_down_never_executes_llm_plan():
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
                "actions": ["turn_on", "turn_off"],
            }
        ],
    }
    by_id = {"tv_quarto": payload["entities"][0]}
    orch = ProgressiveOrchestrator()

    async def interpret_neg(req):
        return _partial(safety=["semantic_negation"], negated=True)

    async def review_fail(req):
        raise AssertionError("must not call review")

    res_neg = asyncio.run(
        orch.resolve(
            text="não liga a tv",
            snapshot_payload=payload,
            snapshot_by_id=by_id,
            origin_area="quarto",
            origin_source="satellite",
            origin_context_id="c1",
            user_id="u1",
            interpret_v5=interpret_neg,
            review_v5=review_fail,
        )
    )
    assert res_neg.outcome_kind == "safety_refusal"
    assert res_neg.operations == ()

    # Decision down, LLM proposes unauthorized/rejected repair -> 0 effects
    async def interpret_partial(req):
        return _partial()

    async def decision_down(p):
        raise RuntimeError("decision offline")

    async def llm_bad_repair(p, d, g):
        return {
            "mode": "repair_proposal",
            "repairs": [{"clause_id": 0, "slot": "action", "value": "unlock", "source": "llm_proposed", "evidence": []}],
        }

    res_bad = asyncio.run(
        orch.resolve(
            text="lingua a tv",
            snapshot_payload=payload,
            snapshot_by_id=by_id,
            origin_area="quarto",
            origin_source="satellite",
            origin_context_id="c1",
            user_id="u1",
            interpret_v5=interpret_partial,
            review_v5=review_fail,
            decision_partial=decision_down,
            llm_handoff=llm_bad_repair,
        )
    )
    assert res_bad.outcome_kind == "clarification"
    assert res_bad.operations == ()
