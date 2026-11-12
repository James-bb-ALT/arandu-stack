"""Tests for local typed Home Graph (exposure, permissions, bounded queries, cache invalidation)."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from custom_components.local_nlu.home_graph import HomeGraph, HomeGraphCache, build_graph_context


def _sample_snapshot(generation: str = "a" * 64) -> dict:
    return {
        "version": 4,
        "generation": generation,
        "areas": [
            {"area_id": "quarto", "names": ["Quarto", "Dormitório"]},
            {"area_id": "sala", "names": ["Sala"]},
        ],
        "entities": [
            {
                "registry_id": "tv_quarto",
                "entity_id": "media_player.tv_quarto",
                "domain": "media_player",
                "area_id": "quarto",
                "device_id": "dev_tv",
                "name": "Televisão",
                "device_name": "TV Quarto",
                "aliases": ["tv"],
                "device_class": "tv",
                "actions": ["turn_on", "turn_off", "play", "pause"],
            },
            {
                "registry_id": "cofre_quarto",
                "entity_id": "lock.cofre_quarto",
                "domain": "lock",
                "area_id": "quarto",
                "device_id": "dev_cofre",
                "name": "Cofre",
                "aliases": [],
                "device_class": "lock",
                "actions": ["lock", "unlock"],
            },
        ],
    }


def test_home_graph_enforces_per_user_allowed_entities_and_bounded_queries():
    graph = HomeGraph.from_snapshot(
        _sample_snapshot(),
        identity_scope_hash="user1",
        floor_by_area={"quarto": ("andar_2", "Segundo Andar")},
        allowed_entity_ids={"media_player.tv_quarto"},
    )
    assert graph.get_entity("media_player.tv_quarto") is not None
    assert graph.get_entity("lock.cofre_quarto") is None
    assert graph.get_capabilities("lock.cofre_quarto") is None
    assert graph.search_entities("cofre") == []
    hits = graph.search_entities("tv", area="quarto", capabilities=["turn_on"])
    assert len(hits) == 1
    assert hits[0]["entity_id"] == "media_player.tv_quarto"
    related = graph.get_related("media_player.tv_quarto", depth=2, limit=16)
    assert any(r["rel"] == "IN_AREA" for r in related)


def test_home_graph_cache_invalidates_on_generation_change():
    cache = HomeGraphCache()
    g1 = cache.get_or_build(_sample_snapshot("a" * 64), identity_scope_hash="u1")
    g1_again = cache.get_or_build(_sample_snapshot("a" * 64), identity_scope_hash="u1")
    assert g1 is g1_again

    g2 = cache.get_or_build(_sample_snapshot("b" * 64), identity_scope_hash="u1")
    assert g2 is not g1
    assert g2.generation == "b" * 64


def test_build_graph_context_is_bounded_and_reads_authorized_state():
    graph = HomeGraph.from_snapshot(_sample_snapshot(), identity_scope_hash="u1")
    partial = {
        "request_kind": "information_request",
        "clauses": [
            {
                "clause_id": 0,
                "original_text": "tv do quarto",
                "slots": {"target": {"status": "unknown", "value": None}, "area": {"status": "resolved", "value": "quarto"}},
            }
        ],
    }
    ctx = build_graph_context(
        graph,
        partial,
        max_rounds=2,
        live_state_reader=lambda eid: {"state": "on", "last_updated": "2026-11-12T10:00:00Z"} if eid == "media_player.tv_quarto" else None,
    )
    assert ctx["rounds_used"] <= 2
    assert any(e["entity_id"] == "media_player.tv_quarto" and e.get("live_state", {}).get("state") == "on" for e in ctx["entities"])
