"""Tests for Decision VNext partial slot repair, PT-BR phonetics, and cheap triage."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "arandu_stack/rootfs/modules/arandu_decision/rootfs/src/src"))

from arandu_decision.partial_reasoner import reason_partial
from arandu_decision.phonetics_ptbr import cheap_triage, phonetic_ptbr, rank_action_candidates


def test_phonetic_ptbr_catches_lingua_to_liga():
    assert phonetic_ptbr("lingua") == phonetic_ptbr("liga")
    ranked = rank_action_candidates("lingua a tv", ["turn_on", "turn_off", "play", "pause"])
    assert ranked[0]["value"] == "turn_on"
    assert ranked[0]["score"] >= 0.85


def test_partial_reasoner_repairs_only_unresolved_action_without_calibration():
    partial = {
        "version": 5,
        "request_id": "req-1",
        "catalog_generation": "a" * 64,
        "identity_scope_hash": "b" * 64,
        "origin": {"area_id": "quarto", "source": "satellite", "context_id": None},
        "request_kind": "possible_device_action",
        "clauses": [
            {
                "clause_id": 0,
                "original_text": "lingua a tv",
                "slots": {
                    "action": {"value": None, "status": "unknown", "evidence": []},
                    "target": {"value": "tv_quarto", "status": "resolved", "evidence": ["authorized_exact_alias:tv"]},
                    "area": {"value": "quarto", "status": "resolved", "evidence": ["entity_area"]},
                    "parameters": {"value": {}, "status": "resolved", "evidence": ["no_parameter_requested"]},
                },
                "allowed_actions": ["turn_on", "turn_off", "play", "pause"],
                "constraints": {"negated": False, "excluded_targets": [], "conditional": False, "quoted_content": False},
                "unresolved": ["action"],
            }
        ],
        "safety_flags": [],
    }
    res = reason_partial(partial)
    assert res["status"] == "resolved_candidate"
    assert res["confidence"] is None
    assert res["calibration_valid"] is False
    assert res["requires_confirmation"] is True
    assert len(res["repairs"]) == 1
    assert res["repairs"][0]["slot"] == "action"
    assert res["repairs"][0]["value"] == "turn_on"


def test_partial_reasoner_clarifies_conflicting_targets_and_routes_conversation():
    conflicting = {
        "version": 5,
        "request_id": "req-2",
        "catalog_generation": "a" * 64,
        "identity_scope_hash": "b" * 64,
        "origin": {"area_id": None, "source": "unknown", "context_id": None},
        "request_kind": "possible_device_action",
        "clauses": [
            {
                "clause_id": 0,
                "original_text": "lingua a tv",
                "slots": {
                    "action": {"value": None, "status": "unknown", "evidence": []},
                    "target": {"value": None, "status": "conflicting", "evidence": [], "candidates": ["tv_sala", "tv_quarto"]},
                },
                "allowed_actions": ["turn_on", "turn_off"],
                "constraints": {"negated": False, "excluded_targets": [], "conditional": False, "quoted_content": False},
                "unresolved": ["action", "target"],
            }
        ],
        "safety_flags": [],
    }
    res = reason_partial(conflicting)
    assert res["status"] == "needs_clarification"
    assert res["reason"] == "ambiguous_target"
    assert res["repairs"] == []

    convo = {
        "version": 5,
        "request_id": "req-3",
        "catalog_generation": "a" * 64,
        "identity_scope_hash": "b" * 64,
        "origin": {"area_id": None, "source": "unknown", "context_id": None},
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
    convo_res = reason_partial(convo)
    assert convo_res["route"] == "general_conversation"
    assert convo_res["status"] == "general_conversation"
    assert convo_res["repairs"] == []
    assert cheap_triage("me diz uma curiosidade sobre TV") == "general_conversation"


def test_partial_reasoner_blocks_safety_or_negated_clauses():
    neg = {
        "version": 5,
        "request_id": "req-4",
        "catalog_generation": "a" * 64,
        "identity_scope_hash": "b" * 64,
        "origin": {"area_id": None, "source": "unknown", "context_id": None},
        "request_kind": "safety_refusal",
        "clauses": [
            {
                "clause_id": 0,
                "original_text": "não liga a tv",
                "slots": {"action": {"value": None, "status": "forbidden", "evidence": ["semantic_negation"]}},
                "allowed_actions": [],
                "constraints": {"negated": True, "excluded_targets": [], "conditional": False, "quoted_content": False},
                "unresolved": [],
            }
        ],
        "safety_flags": ["semantic_negation"],
    }
    res = reason_partial(neg)
    assert res["status"] == "denied"
    assert res["repairs"] == []
