"""Comprehensive V3 verification suite: shared PT-BR semantics, routing, QuerySet, HomeGraph, multi-turn & 664+300+100 corpus."""
from __future__ import annotations

import asyncio
import json
import pathlib
import subprocess
import sys
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "ptbr_v3"


def test_semantics_generator_check_passes():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "generate_semantics.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_generated_semantics_counts_and_non_equivalences():
    from custom_components.local_nlu import generated_semantics_ptbr as gs

    assert gs.CONCEPT_COUNT == 260
    assert gs.FORM_COUNT == 1620
    assert len(gs.AMBIGUITIES) == 42
    assert len(gs.NON_EQUIVALENCES) == 31
    # Non-equivalent pairs must never collapse to identical non-domain action/query concepts
    assert gs.SURFACE_TO_CONCEPTS.get("sensor de temperatura") != gs.SURFACE_TO_CONCEPTS.get("termostato")


def test_fallback_policy_v3_recoverable_routes():
    from custom_components.local_nlu.fallback_policy import RouteCategory, classify_fallback

    r1 = classify_fallback(
        nlu_status="unavailable",
        reason="target_not_found",
        command={"action": "turn_on"},
        candidates=(),
        options=(),
        pending=False,
    )
    assert r1.category == RouteCategory.RECOVERABLE
    assert r1.decision_allowed is True
    assert r1.llm_allowed is True

    r2 = classify_fallback(
        nlu_status="clarification",
        reason="target_not_found",
        command={"action": "turn_off"},
        candidates=("light.a", "light.b"),
        options=(),
        pending=False,
    )
    assert r2.category == RouteCategory.RECOVERABLE
    assert r2.llm_allowed is True

    r_sec = classify_fallback(
        nlu_status="no_match",
        reason="semantic_negation",
        command=None,
        candidates=(),
        options=(),
        pending=False,
    )
    assert r_sec.category == RouteCategory.SAFETY_BLOCK
    assert r_sec.llm_allowed is False


def test_progressive_contract_allows_llm_target_outside_initial_candidates():
    from custom_components.local_nlu.progressive_contract import (
        build_llm_handoff,
        build_origin_context,
        validate_repair_proposal,
    )

    partial = {
        "version": 5,
        "request_id": "req-llm-free",
        "catalog_generation": "a" * 64,
        "identity_scope_hash": "b" * 64,
        "origin": build_origin_context(area_id="sala", source="satellite", context_id="sat-1"),
        "request_kind": "possible_device_action",
        "clauses": [
            {
                "clause_id": 0,
                "original_text": "liga o negocio da sala",
                "slots": {
                    "action": {"value": "turn_on", "status": "resolved", "evidence": ["nlu"]},
                    "target": {"value": None, "status": "candidate", "evidence": [], "candidates": ["light_abajur"]},
                },
                "allowed_actions": ["turn_on", "turn_off"],
                "constraints": {"negated": False, "excluded_targets": ["light_proibida"], "conditional": False, "quoted_content": False},
                "unresolved": ["target"],
            }
        ],
        "safety_flags": [],
    }
    handoff = build_llm_handoff(partial)
    assert handoff["profile"] == "automation"
    assert handoff["preserved_slots"]["action"] == "turn_on"

    # LLM proposes a different authorized target in the catalog
    ok, reason = validate_repair_proposal(
        partial,
        [{"clause_id": 0, "slot": "target", "value": "switch_tv_sala", "source": "llm_proposed"}],
        authorized_targets={"light_abajur", "switch_tv_sala"},
    )
    assert ok is True, reason

    # Excluded target still blocked
    ok_excl, reason_excl = validate_repair_proposal(
        partial,
        [{"clause_id": 0, "slot": "target", "value": "light_proibida", "source": "llm_proposed"}],
        authorized_targets={"light_abajur", "switch_tv_sala", "light_proibida"},
    )
    assert ok_excl is False
    assert reason_excl == "excluded_target_violation"


def test_progressive_orchestrator_escalates_needs_clarification_to_llm():
    from custom_components.local_nlu.progressive_contract import build_origin_context
    from custom_components.local_nlu.progressive_orchestrator import ProgressiveOrchestrator

    async def _run():
        orch = ProgressiveOrchestrator()

        async def interpret_v5(_req):
            return {
                "version": 5,
                "request_id": "req-esc",
                "catalog_generation": "a" * 64,
                "identity_scope_hash": "b" * 64,
                "origin": build_origin_context(area_id="sala", source="satellite"),
                "request_kind": "possible_device_action",
                "clauses": [
                    {
                        "clause_id": 0,
                        "original_text": "liga a tv",
                        "slots": {
                            "action": {"value": "turn_on", "status": "resolved", "evidence": ["nlu"]},
                            "target": {"value": None, "status": "conflicting", "evidence": [], "candidates": ["switch_tv_sala", "switch_tv_quarto"]},
                        },
                        "allowed_actions": ["turn_on", "turn_off"],
                        "constraints": {"negated": False, "excluded_targets": [], "conditional": False, "quoted_content": False},
                        "unresolved": ["target"],
                    }
                ],
                "safety_flags": [],
            }

        async def decision_partial(_req):
            return {"route": "possible_action", "status": "needs_clarification", "reason": "ambiguous_target", "alternatives": [{"value": "switch_tv_sala"}]}

        llm_called = False

        async def llm_handoff(_partial, _dec, _graph):
            nonlocal llm_called
            llm_called = True
            return {
                "mode": "repair_proposal",
                "repairs": [{"clause_id": 0, "slot": "target", "value": "switch_tv_sala", "source": "llm_proposed"}],
            }

        async def review_v5(_req):
            return {
                "accepted": True,
                "requires_confirmation": False,
                "operations": [{"action": "turn_on", "targets": ["switch_tv_sala"], "parameters": {}, "evidence": ["llm_proposed"]}],
            }

        snapshot_payload = {
            "generation": "a" * 64,
            "areas": [{"area_id": "sala", "names": ["Sala"]}, {"area_id": "quarto", "names": ["Quarto"]}],
            "entities": [
                {"entity_id": "switch_tv_sala", "registry_id": "switch_tv_sala", "name": "TV da Sala", "area_id": "sala", "domain": "switch", "aliases": [], "actions": ["turn_on", "turn_off"], "attributes": {"available": True}},
                {"entity_id": "switch_tv_quarto", "registry_id": "switch_tv_quarto", "name": "TV do Quarto", "area_id": "quarto", "domain": "switch", "aliases": [], "actions": ["turn_on", "turn_off"], "attributes": {"available": True}},
            ],
        }
        res = await orch.resolve(
            text="liga a tv",
            snapshot_payload=snapshot_payload,
            snapshot_by_id={e["entity_id"]: e for e in snapshot_payload["entities"]},
            origin_area="sala",
            origin_source="satellite",
            origin_context_id="sat-1",
            user_id="user-1",
            interpret_v5=interpret_v5,
            review_v5=review_v5,
            decision_partial=decision_partial,
            llm_handoff=llm_handoff,
            auto_resolve_level="calibrated",
        )
        assert llm_called is True
        assert res.outcome_kind == "execute_plan"
        assert res.operations[0]["targets"] == ["switch_tv_sala"]

    asyncio.run(_run())


def test_queryset_generic_domains_and_homegraph_expansion():
    from custom_components.local_nlu.home_graph import HomeGraph, expand_graph_context
    from custom_components.local_nlu.queries_set import evaluate_queryset

    payload = {
        "generation": "gen-1",
        "areas": [{"area_id": "sala", "names": ["Sala"]}, {"area_id": "cozinha", "names": ["Cozinha"]}],
        "entities": [
            {"entity_id": "fan.sala", "name": "Ventilador Sala", "area_id": "sala", "domain": "fan", "aliases": [], "actions": ["turn_on", "turn_off"], "attributes": {"available": True}},
            {"entity_id": "lock.porta_entrada", "name": "Fechadura Entrada", "area_id": "sala", "domain": "lock", "aliases": [], "actions": ["lock", "unlock"], "attributes": {"available": True}},
            {"entity_id": "cover.persiana_cozinha", "name": "Persiana Cozinha", "area_id": "cozinha", "domain": "cover", "aliases": [], "actions": ["open", "close"], "attributes": {"available": True}},
        ],
    }
    snap = SimpleNamespace(payload=payload, by_id={e["entity_id"]: e for e in payload["entities"]})
    states = {"fan.sala": "on", "lock.porta_entrada": "unlocked", "cover.persiana_cozinha": "closed"}

    q_fan = evaluate_queryset("quais ventiladores ligados na sala", snap, states_map=states)
    assert q_fan is not None and q_fan["domain"] == "fan" and q_fan["count_active"] == 1

    q_lock = evaluate_queryset("quantas fechaduras destrancadas em casa", snap, states_map=states)
    assert q_lock is not None and q_lock["domain"] == "lock" and q_lock["count_active"] == 1

    graph = HomeGraph.from_snapshot(payload)
    exp = expand_graph_context(graph, ["fan.sala"], depth=2)
    ids = {e["entity_id"] for e in exp["entities"]}
    assert "fan.sala" in ids
    assert "lock.porta_entrada" in ids


def test_pending_request_multiturn_continuity():
    from custom_components.local_nlu.dialogue_coordinator import PendingRequest, PendingRequestStore

    store = PendingRequestStore()
    req = PendingRequest(
        user_id="u1",
        origin_id="sat1",
        conversation_id="c1",
        generation="g1",
        preserved_slots={"action": "turn_on"},
        missing_slots=("target",),
        original_text="liga",
    )
    store.put(req)
    got = store.get_valid(user_id="u1", origin_id="sat1", conversation_id="c1", generation="g1")
    assert got is not None
    assert got.merge_reply("a luz da sala") == "liga (a luz da sala)"
    # Cross-user or stale generation must never leak
    assert store.get_valid(user_id="u2", origin_id="sat1", conversation_id="c1", generation="g1") is None
    assert store.get_valid(user_id="u1", origin_id="sat1", conversation_id="c1", generation="g2") is None


def test_corpus_664_and_300_negatives_and_100_dynamic_aliases():
    from custom_components.local_nlu.home_graph import HomeGraph
    from custom_components.local_nlu.semantic_context import semantic_refusal

    matriz = [json.loads(line) for line in (FIXTURES / "matriz_664.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    negatives = [json.loads(line) for line in (FIXTURES / "negative_counterfactual_300.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    dynamics = [json.loads(line) for line in (FIXTURES / "dynamic_aliases_100.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]

    assert len(matriz) == 664
    assert len({row["family"] for row in matriz}) == 7
    assert len(negatives) >= 300
    assert len(dynamics) >= 100

    # Verify negative/counterfactual safety cases are caught by semantic_refusal or marked unauthorized/non_equivalent
    caught = 0
    for row in negatives:
        if row["family"] in {"semantic_negation", "semantic_condition"}:
            assert semantic_refusal(row["utterance"]) is not None, row
            caught += 1
        elif row["expected_guard"] in {"safety_block", "unauthorized", "non_equivalent"}:
            caught += 1
    assert caught == len(negatives)

    # Verify all 100 invented dynamic aliases are searchable in HomeGraph without hardcoded rules
    dyn_payload = {
        "generation": "dyn-gen",
        "areas": [{"area_id": "area_teste", "names": ["Área Teste"]}],
        "entities": [
            {
                "entity_id": d["entity_id"],
                "name": d["alias"],
                "area_id": d["area_id"],
                "domain": "switch",
                "aliases": [d["alias"]],
                "actions": ["turn_on", "turn_off"],
            }
            for d in dynamics
        ],
    }
    graph = HomeGraph.from_snapshot(dyn_payload)
    for d in dynamics:
        matches = graph.search_entities(d["alias"], limit=5)
        assert any(m["entity_id"] == d["entity_id"] for m in matches), d


def test_ai_lab_pipeline_ambiguous_decision_escalates_to_llm():
    from custom_components.local_nlu.ai_lab_core import pipeline

    async def _run():
        async def nlu(_text):
            return {"version": 4, "status": "no_match", "reason": "unknown_intent"}

        async def decision(_payload):
            return {
                "algorithm": "structured-v3",
                "decision_status": "ambiguous",
                "reason": "insufficient_margin",
                "analysis": {"device": {"candidate_count": 2}},
            }

        async def llm(payload):
            assert payload["decision"]["decision_status"] == "ambiguous"
            return {
                "kind": "conversation",
                "contract_version": 2,
                "plan": None,
                "response_text": "Qual ventilador você quer ligar?",
            }

        res = await pipeline("liga ventilador", nlu, decision, llm, mode="pipeline")
        assert res["stages"]["decision"]["ambiguity_evidence"] is True
        assert res["stages"]["llm"]["status"] == "executed"
        assert res["source"] == "llm"
        assert res["response_text"] == "Qual ventilador você quer ligar?"

    asyncio.run(_run())
