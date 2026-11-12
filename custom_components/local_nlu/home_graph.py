"""Local typed Home Graph built strictly from authorized HA catalog/registries (no Graphify)."""
from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Callable

from .contextual_protocol import normalize

RELATION_ALLOWLIST = frozenset(
    ("IN_AREA", "ON_FLOOR", "HAS_ENTITY", "HAS_CAPABILITY", "ALIAS_OF", "RELATED_TO", "CONTROLS")
)
MAX_DEPTH = 2
MAX_LIMIT = 32
DEFAULT_STATE_TTL_S = 10.0


@dataclass(frozen=True)
class GraphNode:
    node_id: str
    node_type: str
    label: str
    attributes: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.node_id,
            "type": self.node_type,
            "label": self.label,
            "attributes": dict(self.attributes),
        }


@dataclass(frozen=True)
class GraphEdge:
    src: str
    rel: str
    dst: str


class HomeGraph:
    """Per-user authorized read-only typed Home Graph with generation-keyed caching."""

    def __init__(
        self,
        *,
        generation: str,
        identity_scope_hash: str,
        nodes: dict[str, GraphNode],
        edges: list[GraphEdge],
        entity_ids: frozenset[str],
        area_ids: frozenset[str],
        alias_index: dict[str, set[str]],
    ) -> None:
        self.generation = generation
        self.identity_scope_hash = identity_scope_hash
        self._nodes = nodes
        self._edges = edges
        self._entity_ids = entity_ids
        self._area_ids = area_ids
        self._alias_index = alias_index
        self._adj: dict[str, list[GraphEdge]] = {}
        for edge in edges:
            self._adj.setdefault(edge.src, []).append(edge)

    @classmethod
    def from_snapshot(
        cls,
        snapshot_payload: dict[str, Any],
        *,
        identity_scope_hash: str = "",
        floor_by_area: dict[str, tuple[str, str]] | None = None,
        allowed_entity_ids: set[str] | frozenset[str] | None = None,
    ) -> HomeGraph:
        generation = str(snapshot_payload.get("generation") or "")
        nodes: dict[str, GraphNode] = {
            "home:root": GraphNode("home:root", "Home", "Home", {"generation": generation})
        }
        edges: list[GraphEdge] = []
        area_ids: set[str] = set()
        entity_ids: set[str] = set()
        alias_index: dict[str, set[str]] = {}

        floor_map = floor_by_area or {}
        for area in snapshot_payload.get("areas") or ():
            aid = area["area_id"]
            area_ids.add(aid)
            area_node_id = f"area:{aid}"
            names = list(area.get("names") or (aid,))
            nodes[area_node_id] = GraphNode(
                area_node_id, "Area", str(names[0]), {"area_id": aid, "names": names}
            )
            edges.append(GraphEdge("home:root", "HAS_ENTITY", area_node_id))
            if aid in floor_map:
                fid, fname = floor_map[aid]
                fnode = f"floor:{fid}"
                if fnode not in nodes:
                    nodes[fnode] = GraphNode(fnode, "Floor", fname, {"floor_id": fid})
                edges.append(GraphEdge(area_node_id, "ON_FLOOR", fnode))

        for row in snapshot_payload.get("entities") or ():
            eid = row.get("entity_id") or row.get("registry_id")
            if not eid:
                continue
            if allowed_entity_ids is not None and eid not in allowed_entity_ids:
                continue
            entity_ids.add(eid)
            domain = row.get("domain", "")
            ntype = {
                "scene": "Scene",
                "script": "Script",
                "automation": "Automation",
                "sensor": "Sensor",
                "binary_sensor": "Sensor",
                "person": "Person",
            }.get(domain, "Entity")
            enode = f"entity:{eid}"
            actions = sorted(set(row.get("actions") or ()))
            attrs = {
                "entity_id": eid,
                "registry_id": row.get("registry_id", eid),
                "domain": domain,
                "device_class": row.get("device_class"),
                "area_id": row.get("area_id"),
                "device_id": row.get("device_id"),
                "exposed": True,
                "allowed": True,
                "actions": actions,
            }
            nodes[enode] = GraphNode(enode, ntype, str(row.get("name") or eid), attrs)

            area_id = row.get("area_id")
            if area_id and f"area:{area_id}" in nodes:
                edges.append(GraphEdge(enode, "IN_AREA", f"area:{area_id}"))
                edges.append(GraphEdge(f"area:{area_id}", "HAS_ENTITY", enode))

            dev_id = row.get("device_id")
            if dev_id:
                dnode = f"device:{dev_id}"
                if dnode not in nodes:
                    nodes[dnode] = GraphNode(
                        dnode, "Device", str(row.get("device_name") or dev_id), {"device_id": dev_id}
                    )
                edges.append(GraphEdge(dnode, "HAS_ENTITY", enode))

            for cap in actions:
                cnode = f"cap:{cap}"
                if cnode not in nodes:
                    nodes[cnode] = GraphNode(cnode, "Capability", cap, {"capability": cap})
                edges.append(GraphEdge(enode, "HAS_CAPABILITY", cnode))

            for alias in [row.get("name"), *(row.get("aliases") or ()), row.get("device_name")]:
                if not alias:
                    continue
                norm = normalize(str(alias))
                if not norm:
                    continue
                anode = f"alias:{norm}"
                if anode not in nodes:
                    nodes[anode] = GraphNode(anode, "Alias", str(alias), {"normalized": norm})
                edges.append(GraphEdge(anode, "ALIAS_OF", enode))
                for tok in norm.split():
                    alias_index.setdefault(tok, set()).add(eid)
                alias_index.setdefault(norm, set()).add(eid)

        return cls(
            generation=generation,
            identity_scope_hash=identity_scope_hash,
            nodes=nodes,
            edges=edges,
            entity_ids=frozenset(entity_ids),
            area_ids=frozenset(area_ids),
            alias_index=alias_index,
        )

    def search_entities(
        self,
        query: str,
        *,
        domain: str | None = None,
        area: str | None = None,
        capabilities: list[str] | tuple[str, ...] | None = None,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), MAX_LIMIT))
        norm_q = normalize(query)
        tokens = [t for t in norm_q.split() if len(t) >= 2]
        req_caps = set(capabilities or ())
        scores: dict[str, int] = {}
        if norm_q in self._alias_index:
            for eid in self._alias_index[norm_q]:
                scores[eid] = scores.get(eid, 0) + 5
        for tok in tokens:
            for eid in self._alias_index.get(tok, ()):
                scores[eid] = scores.get(eid, 0) + 1
        if not tokens:
            for eid in self._entity_ids:
                scores.setdefault(eid, 1)

        results: list[tuple[int, str, dict[str, Any]]] = []
        for eid, score in scores.items():
            node = self._nodes.get(f"entity:{eid}")
            if not node:
                continue
            attrs = node.attributes
            if domain and attrs.get("domain") != domain:
                continue
            if area and attrs.get("area_id") != area:
                continue
            if req_caps and not req_caps.issubset(set(attrs.get("actions") or ())):
                continue
            results.append(
                (
                    score,
                    eid,
                    {
                        "entity_id": eid,
                        "registry_id": attrs.get("registry_id", eid),
                        "name": node.label,
                        "domain": attrs.get("domain"),
                        "area_id": attrs.get("area_id"),
                        "device_class": attrs.get("device_class"),
                        "actions": list(attrs.get("actions") or ()),
                    },
                )
            )
        results.sort(key=lambda item: (-item[0], item[1]))
        return [row for _, _, row in results[:limit]]

    def get_entity(self, entity_id: str) -> dict[str, Any] | None:
        if entity_id not in self._entity_ids:
            return None
        node = self._nodes.get(f"entity:{entity_id}")
        return node.as_dict() if node else None

    def get_area(self, area_id: str) -> dict[str, Any] | None:
        if area_id not in self._area_ids:
            return None
        node = self._nodes.get(f"area:{area_id}")
        return node.as_dict() if node else None

    def get_capabilities(self, entity_id: str) -> list[str] | None:
        if entity_id not in self._entity_ids:
            return None
        node = self._nodes.get(f"entity:{entity_id}")
        return list(node.attributes.get("actions") or ()) if node else None

    def get_related(
        self,
        entity_id: str,
        *,
        relation_allowlist: set[str] | frozenset[str] | None = None,
        depth: int = 1,
        limit: int = 16,
    ) -> list[dict[str, Any]]:
        if entity_id not in self._entity_ids:
            return []
        depth = max(1, min(int(depth), MAX_DEPTH))
        limit = max(1, min(int(limit), MAX_LIMIT))
        allowed_rels = (set(relation_allowlist) & RELATION_ALLOWLIST) if relation_allowlist else RELATION_ALLOWLIST

        visited = {f"entity:{entity_id}"}
        frontier = [f"entity:{entity_id}"]
        out: list[dict[str, Any]] = []
        for _ in range(depth):
            next_frontier: list[str] = []
            for cur in frontier:
                for edge in self._adj.get(cur, ()):
                    if edge.rel not in allowed_rels:
                        continue
                    dst_node = self._nodes.get(edge.dst)
                    if not dst_node:
                        continue
                    if dst_node.node_type in {"Entity", "Scene", "Script", "Automation", "Sensor", "Person"}:
                        eid = dst_node.attributes.get("entity_id")
                        if eid not in self._entity_ids:
                            continue
                    out.append({"src": edge.src, "rel": edge.rel, "dst": edge.dst, "node": dst_node.as_dict()})
                    if len(out) >= limit:
                        return out
                    if edge.dst not in visited:
                        visited.add(edge.dst)
                        next_frontier.append(edge.dst)
            frontier = next_frontier
            if not frontier:
                break
        return out

    def get_state(
        self,
        entity_id: str,
        *,
        live_state_reader: Callable[[str], dict[str, Any] | None] | None = None,
    ) -> dict[str, Any] | None:
        if entity_id not in self._entity_ids:
            return None
        if live_state_reader is None:
            return None
        state = live_state_reader(entity_id)
        if not isinstance(state, dict):
            return None
        return {
            "entity_id": entity_id,
            "state": state.get("state"),
            "observed_at": state.get("last_updated") or time.time(),
        }

    def get_graph_summary(self) -> dict[str, Any]:
        return {
            "generation": self.generation,
            "identity_scope_hash": self.identity_scope_hash,
            "areas": len(self._area_ids),
            "entities": len(self._entity_ids),
            "tools": [
                "search_entities",
                "get_entity",
                "get_area",
                "get_capabilities",
                "get_related",
                "get_state",
            ],
        }


class HomeGraphCache:
    """Generation- and identity-keyed cache that invalidates on any catalog change."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], HomeGraph] = {}

    def get_or_build(
        self,
        snapshot_payload: dict[str, Any],
        *,
        identity_scope_hash: str = "",
        floor_by_area: dict[str, tuple[str, str]] | None = None,
        allowed_entity_ids: set[str] | frozenset[str] | None = None,
    ) -> HomeGraph:
        generation = str(snapshot_payload.get("generation") or "")
        key = (generation, identity_scope_hash)
        if key not in self._entries:
            # Evict stale generations for same identity to bound RAM
            for existing_key in [k for k in self._entries if k[1] == identity_scope_hash and k[0] != generation]:
                self._entries.pop(existing_key, None)
            self._entries[key] = HomeGraph.from_snapshot(
                snapshot_payload,
                identity_scope_hash=identity_scope_hash,
                floor_by_area=floor_by_area,
                allowed_entity_ids=allowed_entity_ids,
            )
        return self._entries[key]

    def invalidate(self, generation: str | None = None) -> None:
        if generation is None:
            self._entries.clear()
        else:
            for key in [k for k in self._entries if k[0] == generation]:
                self._entries.pop(key, None)


def build_graph_context(
    graph: HomeGraph,
    partial: dict[str, Any],
    *,
    max_rounds: int = 2,
    live_state_reader: Callable[[str], dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """Perform bounded read-only graph retrieval (<= max_rounds) for LLM handoff."""
    rounds_used = 0
    matched_entities: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    areas_seen: dict[str, dict[str, Any]] = {}

    for clause in partial.get("clauses") or ():
        slots = clause.get("slots") or {}
        area_val = (slots.get("area") or {}).get("value")
        if isinstance(area_val, str):
            area_node = graph.get_area(area_val)
            if area_node:
                areas_seen[area_val] = area_node

        target_slot = slots.get("target") or {}
        if target_slot.get("status") == "resolved" and isinstance(target_slot.get("value"), str):
            ent = graph.get_entity(target_slot["value"])
            if ent and target_slot["value"] not in seen_ids:
                seen_ids.add(target_slot["value"])
                matched_entities.append(ent["attributes"])
        elif target_slot.get("candidates"):
            for cid in target_slot["candidates"][:8]:
                if isinstance(cid, str) and cid not in seen_ids:
                    ent = graph.get_entity(cid)
                    if ent:
                        seen_ids.add(cid)
                        matched_entities.append(ent["attributes"])

    if not matched_entities and rounds_used < max_rounds:
        rounds_used += 1
        query_text = " ".join(c.get("original_text", "") for c in partial.get("clauses") or ())
        for row in graph.search_entities(query_text, limit=8):
            eid = row["entity_id"]
            if eid not in seen_ids:
                seen_ids.add(eid)
                matched_entities.append(row)

    if partial.get("request_kind") == "information_request" and matched_entities and rounds_used < max_rounds:
        rounds_used += 1
        for row in matched_entities[:4]:
            st = graph.get_state(row["entity_id"], live_state_reader=live_state_reader)
            if st:
                row["live_state"] = st

    return {
        "summary": graph.get_graph_summary(),
        "rounds_used": rounds_used,
        "max_rounds": max_rounds,
        "entities": matched_entities[:12],
        "areas": list(areas_seen.values()),
    }
