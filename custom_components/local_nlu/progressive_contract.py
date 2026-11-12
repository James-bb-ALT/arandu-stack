"""Additive v5 progressive resolution contract between NLU, Decision, LLM, and HA runtime."""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from .capabilities import ACTIONS
from .contextual_protocol import identifier, normalize, text as valid_wire_text, validate_parameters
from .protocol import ProtocolError

PROGRESSIVE_PROTOCOL_VERSION = 5
MAX_TEXT_CHARS = 512
MAX_CLAUSES = 4
MAX_TARGETS = 32

SLOT_STATUSES = frozenset(("resolved", "candidate", "unknown", "conflicting", "forbidden"))
PROVENANCE_SOURCES = frozenset(
    (
        "nlu_rule",
        "catalog_exact",
        "catalog_alias",
        "contextual_reference",
        "decision_ranked",
        "llm_proposed",
    )
)
REQUEST_KINDS = frozenset(
    (
        "resolved_plan",
        "possible_device_action",
        "information_request",
        "general_conversation",
        "unknown",
        "safety_refusal",
        "clarification_needed",
    )
)

_ALLOWED_REPAIR_SLOTS = frozenset(("action", "target", "area", "parameters"))
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class SlotState:
    value: Any = None
    status: str = "unknown"
    evidence: tuple[str, ...] = ()
    source: str | None = None
    candidates: tuple[Any, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "value": self.value,
            "status": self.status,
            "evidence": list(self.evidence),
        }
        if self.source is not None:
            payload["source"] = self.source
        if self.candidates:
            payload["candidates"] = list(self.candidates)
        return payload


@dataclass(frozen=True)
class ClauseConstraints:
    negated: bool = False
    excluded_targets: tuple[str, ...] = ()
    conditional: bool = False
    quoted_content: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "negated": self.negated,
            "excluded_targets": list(self.excluded_targets),
            "conditional": self.conditional,
            "quoted_content": self.quoted_content,
        }


@dataclass(frozen=True)
class PartialClause:
    clause_id: int
    original_text: str
    slots: dict[str, SlotState]
    allowed_actions: tuple[str, ...] = ()
    constraints: ClauseConstraints = field(default_factory=ClauseConstraints)
    unresolved: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "clause_id": self.clause_id,
            "original_text": self.original_text,
            "slots": {k: v.as_dict() for k, v in self.slots.items()},
            "allowed_actions": list(self.allowed_actions),
            "constraints": self.constraints.as_dict(),
            "unresolved": list(self.unresolved),
        }


@dataclass(frozen=True)
class OriginContext:
    area_id: str | None = None
    source: str = "unknown"
    context_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "area_id": self.area_id,
            "source": self.source,
            "context_id": self.context_id,
        }


@dataclass(frozen=True)
class PartialUnderstanding:
    version: int
    request_id: str
    catalog_generation: str
    identity_scope_hash: str
    origin: OriginContext
    request_kind: str
    clauses: tuple[PartialClause, ...]
    safety_flags: tuple[str, ...] = ()
    literal_segments: tuple[str, ...] = ()
    criticality: str = "normal"
    freshness: str = "current"
    v4_outcome: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "version": self.version,
            "request_id": self.request_id,
            "catalog_generation": self.catalog_generation,
            "identity_scope_hash": self.identity_scope_hash,
            "origin": self.origin.as_dict(),
            "request_kind": self.request_kind,
            "clauses": [c.as_dict() for c in self.clauses],
            "safety_flags": list(self.safety_flags),
            "literal_segments": list(self.literal_segments),
            "criticality": self.criticality,
            "freshness": self.freshness,
        }
        if self.v4_outcome is not None:
            payload["v4_outcome"] = self.v4_outcome
        return payload


@dataclass(frozen=True)
class ProposalRepair:
    clause_id: int
    slot: str
    value: Any
    source: str
    evidence: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "clause_id": self.clause_id,
            "slot": self.slot,
            "value": self.value,
            "source": self.source,
            "evidence": list(self.evidence),
        }


def validate_partial_understanding(payload: Any) -> dict[str, Any]:
    """Validate a v5 PartialUnderstanding envelope strictly."""
    if type(payload) is not dict:
        raise ProtocolError("partial_understanding")
    if payload.get("version") != PROGRESSIVE_PROTOCOL_VERSION:
        raise ProtocolError("version")
    request_id = payload.get("request_id")
    if not valid_wire_text(request_id, 128):
        raise ProtocolError("request_id")
    generation = payload.get("catalog_generation")
    if type(generation) is not str or not _SHA256_RE.match(generation):
        raise ProtocolError("catalog_generation")
    identity_hash = payload.get("identity_scope_hash")
    if type(identity_hash) is not str or not _SHA256_RE.match(identity_hash):
        raise ProtocolError("identity_scope_hash")
    origin = payload.get("origin")
    if type(origin) is not dict:
        raise ProtocolError("origin")
    if origin.get("source") not in {"satellite", "device", "default", "unknown"}:
        raise ProtocolError("origin_source")
    request_kind = payload.get("request_kind")
    if request_kind not in REQUEST_KINDS:
        raise ProtocolError("request_kind")
    clauses = payload.get("clauses")
    if type(clauses) is not list or not (1 <= len(clauses) <= MAX_CLAUSES):
        raise ProtocolError("clauses")
    for idx, clause in enumerate(clauses):
        if type(clause) is not dict or clause.get("clause_id") != idx:
            raise ProtocolError("clause_id")
        orig = clause.get("original_text")
        if type(orig) is not str or not orig or len(orig) > MAX_TEXT_CHARS or any(ord(ch) < 32 or ord(ch) == 127 for ch in orig):
            raise ProtocolError("original_text")
        slots = clause.get("slots")
        if type(slots) is not dict:
            raise ProtocolError("slots")
        for slot_name, slot in slots.items():
            if slot_name not in _ALLOWED_REPAIR_SLOTS or type(slot) is not dict:
                raise ProtocolError("slot")
            if slot.get("status") not in SLOT_STATUSES:
                raise ProtocolError("slot_status")
            source = slot.get("source")
            if source is not None and source not in PROVENANCE_SOURCES:
                raise ProtocolError("slot_source")
            ev = slot.get("evidence", [])
            if type(ev) is not list or any(not valid_wire_text(item, 128) for item in ev):
                raise ProtocolError("evidence")
            if slot_name == "parameters" and slot.get("value") not in (None, {}):
                validate_parameters(slot["value"])
        allowed_actions = clause.get("allowed_actions", [])
        if type(allowed_actions) is not list or any(action not in ACTIONS for action in allowed_actions):
            raise ProtocolError("allowed_actions")
        constraints = clause.get("constraints")
        if type(constraints) is not dict:
            raise ProtocolError("constraints")
        excluded = constraints.get("excluded_targets", [])
        if type(excluded) is not list or len(excluded) > MAX_TARGETS or any(not identifier(t) for t in excluded):
            raise ProtocolError("excluded_targets")
        unresolved = clause.get("unresolved", [])
        if type(unresolved) is not list or any(s not in _ALLOWED_REPAIR_SLOTS for s in unresolved):
            raise ProtocolError("unresolved")
    return payload


def validate_repair_proposal(
    partial: dict[str, Any],
    repairs: list[dict[str, Any]],
    *,
    authorized_targets: set[str] | frozenset[str] | None = None,
) -> tuple[bool, str | None]:
    """Deterministically enforce slot immutability, constraints, and allowed capabilities."""
    validate_partial_understanding(partial)
    if type(repairs) is not list or not repairs:
        return False, "empty_repairs"
    safety_flags = set(partial.get("safety_flags") or ())
    if safety_flags:
        return False, "safety_locked"

    clauses_by_id = {c["clause_id"]: c for c in partial["clauses"]}
    for repair in repairs:
        if type(repair) is not dict:
            return False, "invalid_repair"
        clause_id = repair.get("clause_id")
        if clause_id not in clauses_by_id:
            return False, "invalid_clause_id"
        clause = clauses_by_id[clause_id]
        constraints = clause["constraints"]
        if constraints.get("negated") or constraints.get("conditional") or constraints.get("quoted_content"):
            return False, "constraint_locked"
        slot_name = repair.get("slot")
        if slot_name not in _ALLOWED_REPAIR_SLOTS:
            return False, "invalid_slot"
        source = repair.get("source")
        if source not in {"decision_ranked", "llm_proposed"}:
            return False, "invalid_repair_source"
        slot = clause["slots"].get(slot_name)
        if slot is None:
            return False, "missing_slot"
        if slot["status"] in {"resolved", "conflicting", "forbidden"}:
            return False, f"immutable_slot:{slot_name}"
        if slot_name not in clause.get("unresolved", []) and slot["status"] not in {"unknown", "candidate"}:
            return False, f"slot_not_repairable:{slot_name}"

        value = repair.get("value")
        if slot_name == "action":
            if type(value) is not str or value not in ACTIONS:
                return False, "invalid_action"
            allowed = clause.get("allowed_actions") or []
            if allowed and value not in allowed:
                return False, "action_not_allowed_for_target"
        elif slot_name == "target":
            targets = [value] if type(value) is str else (value if type(value) is list else [])
            if not targets or len(targets) > MAX_TARGETS or any(not identifier(t) for t in targets):
                return False, "invalid_target"
            excluded = set(constraints.get("excluded_targets") or ())
            if any(t in excluded for t in targets):
                return False, "excluded_target_violation"
            candidates = set(slot.get("candidates") or ())
            if candidates and any(t not in candidates for t in targets):
                return False, "target_not_in_candidates"
            if authorized_targets is not None and any(t not in authorized_targets for t in targets):
                return False, "unauthorized_target"
        elif slot_name == "parameters":
            try:
                validate_parameters(value)
            except ProtocolError:
                return False, "invalid_parameters"
        elif slot_name == "area":
            if not valid_wire_text(value, 128):
                return False, "invalid_area"
    return True, None


def build_origin_context(
    *,
    area_id: str | None = None,
    source: str | None = None,
    context_id: str | None = None,
) -> dict[str, Any]:
    normalized_source = source if source in {"satellite", "device", "default", "unknown"} else "unknown"
    return OriginContext(area_id=area_id, source=normalized_source, context_id=context_id).as_dict()


def normalize_repair_text(value: str) -> str:
    return normalize(value)
