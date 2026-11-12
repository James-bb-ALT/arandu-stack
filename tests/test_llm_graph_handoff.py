"""Tests for LLM handoff with PartialUnderstanding and authorized graph_context."""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "arandu_stack/rootfs/modules/arandu_llm/rootfs/src/src"))

from arandu_llm.automation_contract import analyze
from arandu_llm.structured import context_for, infer


def _catalog() -> dict:
    return {
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
                "actions": ["turn_on", "turn_off", "play"],
                "attributes": {"available": True},
            }
        ],
    }


def test_context_for_includes_partial_decision_and_graph_context():
    catalog = _catalog()
    analysis = analyze("lingua a tv", catalog, {})
    partial = {
        "request_kind": "possible_device_action",
        "clauses": [
            {
                "clause_id": 0,
                "unresolved": ["action"],
                "allowed_actions": ["turn_on", "turn_off"],
                "constraints": {"negated": False},
                "slots": {"target": {"status": "resolved", "value": "media_player.tv_quarto"}},
            }
        ],
    }
    decision = {"route": "possible_action", "status": "needs_clarification", "reason": "low_margin", "alternatives": []}
    graph_context = {"summary": {"entities": 1}, "rounds_used": 1, "entities": [{"entity_id": "media_player.tv_quarto"}]}

    content, ids = context_for(
        "lingua a tv",
        catalog,
        analysis,
        {},
        decision,
        partial_understanding=partial,
        graph_context=graph_context,
    )
    assert "media_player.tv_quarto" in ids
    assert "partial_understanding" in content
    assert content["decision_evidence"]["route"] == "possible_action"
    assert content["graph_context"]["rounds_used"] == 1


def test_infer_refuses_safety_locked_partial_without_calling_llama():
    class DummyAdapter:
        phase = "idle"
        threads = 2
        settings = {"llm_model": "tucano", "profiles": {}, "temperature": 0.1, "max_tokens": 128}

    res = asyncio.run(
        infer(
            DummyAdapter(),
            {
                "text": "não liga a tv",
                "partial_understanding": {"request_kind": "safety_refusal", "safety_flags": ["semantic_negation"]},
            },
            _catalog(),
        )
    )
    assert res["mode"] == "refusal"
    assert res["reason"] == "safety_locked"
