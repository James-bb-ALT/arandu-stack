"""Universal progressive pipeline orchestrator: NLU v5 -> Decision VNext -> NLU Review -> LLM + Home Graph -> NLU Review."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import time
import uuid
from typing import Any, Awaitable, Callable

from .contextual_errors import render_contextual_message
from .home_graph import HomeGraphCache, build_graph_context
from .progressive_contract import (
    PROGRESSIVE_PROTOCOL_VERSION,
    build_origin_context,
    validate_repair_proposal,
)


_AMBIGUITY_REASONS = {"ambiguous_area", "unknown_area", "ambiguous_target", "missing_origin_area", "no_compatible_capability", "target_not_found"}


def _area_display_name(area_id: str | None, snapshot_payload: dict[str, Any]) -> str | None:
    if not area_id:
        return None
    for row in snapshot_payload.get("areas") or ():
        if row.get("area_id") == area_id:
            names = row.get("names") or ()
            if names:
                return str(names[0])
    return area_id


def _build_slot_clarification(
    partial: dict[str, Any],
    reason: str | None,
    snapshot_by_id: dict[str, dict],
    snapshot_payload: dict[str, Any],
    custom_text: str | None = None,
) -> tuple[str, str, str, tuple[str, ...], tuple[str, ...]]:
    clauses = partial.get("clauses") or ()
    clause = clauses[0] if clauses else {}
    slots = clause.get("slots") or {}
    unresolved = tuple(clause.get("unresolved") or ())
    v4_out = partial.get("v4_outcome") or {}
    v4_cmd = v4_out.get("command") or {}

    action = (slots.get("action") or {}).get("value") or v4_cmd.get("action")
    target_slot = slots.get("target") or {}
    area_slot = slots.get("area") or {}
    area_id = area_slot.get("value") or v4_cmd.get("area") or (partial.get("origin") or {}).get("area_id")
    area_name = _area_display_name(area_id, snapshot_payload)

    target_cands = tuple(str(c) for c in (target_slot.get("candidates") or v4_out.get("candidates") or ()) if isinstance(c, str))
    candidate_names = [snapshot_by_id[c]["name"] for c in target_cands if c in snapshot_by_id]
    target_val = target_slot.get("value")
    target_names: list[str] = []
    if isinstance(target_val, str) and target_val in snapshot_by_id:
        target_names = [snapshot_by_id[target_val]["name"]]
    elif isinstance(target_val, list):
        target_names = [snapshot_by_id[t]["name"] for t in target_val if isinstance(t, str) and t in snapshot_by_id]
    elif len(candidate_names) == 1 and not action:
        target_names = [candidate_names[0]]

    domains = v4_cmd.get("domains") or ()
    domain = domains[0] if len(domains) == 1 else (
        snapshot_by_id[target_val].get("domain")
        if isinstance(target_val, str) and target_val in snapshot_by_id
        else (snapshot_by_id[target_cands[0]].get("domain") if target_cands and target_cands[0] in snapshot_by_id else None)
    )
    mention = v4_cmd.get("mention")

    effective_reason = reason
    if effective_reason in ("ambiguous_area", "unknown_area", "missing_origin_area") or ("area" in unresolved and area_id is None):
        question_kind = "ask_area"
        missing = ("area",)
    elif target_cands and (effective_reason in ("ambiguous_target", "ambiguous_source") or target_slot.get("status") == "conflicting"):
        question_kind = "disambiguate_target"
        effective_reason = effective_reason or "ambiguous_target"
        missing = ("target",)
    elif target_names and not action and ("action" in unresolved or (slots.get("action") or {}).get("status") == "unknown"):
        question_kind = "ask_action"
        effective_reason = "action_unresolved"
        missing = ("action",)
    elif effective_reason in ("no_compatible_capability", "capability_unsupported"):
        question_kind = "ask_target"
        missing = unresolved or ("target",)
    else:
        question_kind = "ask_target" if action else "clarify"
        if effective_reason in (None, "llm_abstention", "no_repair_produced", "unresolved_progressive_pipeline"):
            effective_reason = "target_not_found" if action else ("action_unresolved" if target_names else "unresolved_progressive_pipeline")
        missing = unresolved or (("target",) if action else ("action", "target"))

    speech_text = custom_text or render_contextual_message(
        effective_reason,
        code="missing_slot",
        mention=mention,
        action=action,
        area_name=area_name,
        candidate_names=candidate_names,
        target_names=target_names,
        domain=domain,
    )
    return speech_text, effective_reason or "clarification", question_kind, missing, target_cands


@dataclass
class ProgressiveResult:
    outcome_kind: str  # "nlu_direct" | "safety_refusal" | "execute_plan" | "confirmation_required" | "clarification" | "conversation_response" | "refusal"
    operations: tuple[dict[str, Any], ...] = ()
    speech: str | None = None
    reason: str | None = None
    trace: dict[str, Any] = field(default_factory=dict)
    question_kind: str | None = None
    missing_slots: tuple[str, ...] = ()
    candidates: tuple[str, ...] = ()


def _identity_scope_hash(user_id: str | None, generation: str) -> str:
    raw = f"{user_id or 'anonymous'}:{generation}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _format_confirmation_prompt(operations: list[dict[str, Any]], snapshot_by_id: dict[str, dict]) -> str:
    if not operations:
        return "Você confirma esta ação?"
    op = operations[0]
    action_labels = {
        "turn_on": "ligar",
        "turn_off": "desligar",
        "open": "abrir",
        "close": "fechar",
        "play": "tocar",
        "pause": "pausar",
        "stop": "parar",
        "lock": "trancar",
        "unlock": "destrancar",
        "brightness": "ajustar o brilho de",
    }
    verb = action_labels.get(op.get("action", ""), op.get("action", "executar"))
    targets = op.get("targets") or []
    if len(targets) == 1 and targets[0] in snapshot_by_id:
        name = snapshot_by_id[targets[0]].get("name") or targets[0]
        return f"Quer {verb} {name}?"
    return f"Quer confirmar {verb}?"


class ProgressiveOrchestrator:
    """Coordinates NLU v5, Decision VNext, Home Graph, and LLM without bypassing NLU review."""

    def __init__(self, *, graph_cache: HomeGraphCache | None = None) -> None:
        self._graph_cache = graph_cache or HomeGraphCache()

    async def resolve(
        self,
        *,
        text: str,
        snapshot_payload: dict[str, Any],
        snapshot_by_id: dict[str, dict],
        origin_area: str | None,
        origin_source: str,
        origin_context_id: str | None,
        user_id: str | None,
        interpret_v5: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
        review_v5: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
        decision_partial: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
        llm_handoff: Callable[[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None], Awaitable[dict[str, Any]]] | None = None,
        live_state_reader: Callable[[str], dict[str, Any] | None] | None = None,
        decision_enabled: bool = True,
        llm_enabled: bool = True,
        auto_resolve_level: str = "conservative",
        last_area: str | None = None,
        last_targets: list[str] | None = None,
    ) -> ProgressiveResult:
        started = time.perf_counter()
        generation = str(snapshot_payload.get("generation") or "")
        identity_hash = _identity_scope_hash(user_id, generation)
        req_id = str(uuid.uuid4())

        partial_req = {
            "version": PROGRESSIVE_PROTOCOL_VERSION,
            "request_id": req_id,
            "generation": generation,
            "identity_scope_hash": identity_hash,
            "text": text,
            "origin_area": origin_area,
            "origin_area_source": origin_source,
            "origin_context_id": origin_context_id,
            "last_area": last_area,
            "last_targets": list(last_targets or ()),
        }
        partial = await interpret_v5(partial_req)
        trace: dict[str, Any] = {
            "request_id": req_id,
            "catalog_generation": generation,
            "request_kind": partial.get("request_kind"),
            "clauses": [
                {
                    "clause_id": c.get("clause_id"),
                    "frozen_slots": {
                        k: v.get("value")
                        for k, v in (c.get("slots") or {}).items()
                        if v.get("status") == "resolved"
                    },
                    "unresolved": c.get("unresolved", []),
                    "allowed_actions": c.get("allowed_actions", []),
                }
                for c in (partial.get("clauses") or ())
            ],
            "decision": None,
            "nlu_review": None,
            "graph_rounds": 0,
            "llm_mode": None,
            "final_outcome": None,
        }

        kind = partial.get("request_kind")
        v4_reason = (partial.get("v4_outcome") or {}).get("reason")
        if kind == "resolved_plan":
            ops = tuple((partial.get("v4_outcome") or {}).get("operations") or ())
            trace["final_outcome"] = "nlu_direct"
            trace["total_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
            return ProgressiveResult("nlu_direct", operations=ops, trace=trace)

        if (kind == "safety_refusal" or partial.get("safety_flags")) and v4_reason not in _AMBIGUITY_REASONS:
            flags = partial.get("safety_flags") or []
            reason = flags[0] if flags else "safety_refusal"
            trace["final_outcome"] = "safety_refusal"
            trace["total_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
            return ProgressiveResult("safety_refusal", reason=reason, trace=trace)

        decision_out: dict[str, Any] | None = None
        if decision_enabled and decision_partial is not None:
            try:
                decision_out = await decision_partial({"partial_understanding": partial})
                trace["decision"] = {
                    "route": decision_out.get("route"),
                    "status": decision_out.get("status"),
                    "reason": decision_out.get("reason"),
                    "calibration_valid": decision_out.get("calibration_valid", False),
                }
            except Exception as exc:
                trace["decision"] = {"status": "error", "reason": type(exc).__name__}
                decision_out = None

        if decision_out:
            d_status = decision_out.get("status")
            if d_status == "denied":
                if v4_reason in _AMBIGUITY_REASONS or not partial.get("safety_flags"):
                    speech_text, eff_reason, q_kind, missing, cands = _build_slot_clarification(
                        partial, v4_reason or decision_out.get("reason"), snapshot_by_id, snapshot_payload
                    )
                    trace["final_outcome"] = "clarification"
                    trace["total_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
                    return ProgressiveResult(
                        "clarification",
                        speech=speech_text,
                        reason=eff_reason,
                        trace=trace,
                        question_kind=q_kind,
                        missing_slots=missing,
                        candidates=cands,
                    )
                trace["final_outcome"] = "safety_refusal"
                trace["total_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
                return ProgressiveResult("safety_refusal", reason=decision_out.get("reason"), trace=trace)

            if d_status == "needs_clarification" and not (llm_enabled and llm_handoff is not None):
                speech_text, eff_reason, q_kind, missing, cands = _build_slot_clarification(
                    partial, decision_out.get("reason") or v4_reason, snapshot_by_id, snapshot_payload
                )
                trace["final_outcome"] = "clarification"
                trace["total_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
                return ProgressiveResult(
                    "clarification",
                    speech=speech_text,
                    reason=eff_reason,
                    trace=trace,
                    question_kind=q_kind,
                    missing_slots=missing,
                    candidates=cands,
                )

            if d_status == "resolved_candidate" and decision_out.get("repairs"):
                repairs = decision_out["repairs"]
                ok, reason = validate_repair_proposal(
                    partial, repairs, authorized_targets=set(snapshot_by_id)
                )
                if ok:
                    wire_repairs = [
                        {
                            "clause_id": r["clause_id"],
                            "slot": r["slot"],
                            "value": r["value"],
                            "source": r["source"],
                            "evidence": list(r.get("evidence") or ()),
                        }
                        for r in repairs
                    ]
                    reviewed = await review_v5(
                        {"version": PROGRESSIVE_PROTOCOL_VERSION, "partial": partial, "repairs": wire_repairs}
                    )
                    trace["nlu_review"] = {
                        "accepted": reviewed.get("accepted"),
                        "reason": reviewed.get("reason"),
                        "requires_confirmation": reviewed.get("requires_confirmation"),
                    }
                    if reviewed.get("accepted"):
                        ops = tuple(reviewed.get("operations") or ())
                        deterministic_grounding = (
                            not bool(reviewed.get("requires_confirmation"))
                            and not bool(decision_out.get("requires_confirmation", True))
                            and bool(decision_out.get("calibration_valid"))
                            and decision_out.get("reason") == "deterministic_catalog_grounding"
                        )
                        needs_confirm = (
                            bool(reviewed.get("requires_confirmation"))
                            or bool(decision_out.get("requires_confirmation", True))
                            or not bool(decision_out.get("calibration_valid"))
                            or (auto_resolve_level != "calibrated" and not deterministic_grounding)
                        )
                        outcome_kind = "confirmation_required" if needs_confirm else "execute_plan"
                        trace["final_outcome"] = outcome_kind
                        trace["total_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
                        return ProgressiveResult(
                            outcome_kind,
                            operations=ops,
                            speech=_format_confirmation_prompt(list(ops), snapshot_by_id) if needs_confirm else None,
                            reason=decision_out.get("reason"),
                            trace=trace,
                        )
                else:
                    trace["nlu_review"] = {"accepted": False, "reason": reason}

        # LLM Handoff (either general_conversation or unresolved repair)
        if llm_enabled and llm_handoff is not None:
            graph = self._graph_cache.get_or_build(
                snapshot_payload,
                identity_scope_hash=identity_hash,
                allowed_entity_ids=frozenset(snapshot_by_id),
            )
            is_convo = bool(decision_out and decision_out.get("route") == "general_conversation")
            graph_ctx = (
                None
                if is_convo
                else build_graph_context(
                    graph, partial, max_rounds=2, live_state_reader=live_state_reader
                )
            )
            trace["graph_rounds"] = (graph_ctx or {}).get("rounds_used", 0)
            llm_out = await llm_handoff(partial, decision_out, graph_ctx)
            mode = llm_out.get("mode")
            trace["llm_mode"] = mode

            if mode == "conversation_response" and llm_out.get("response_text"):
                trace["final_outcome"] = "conversation_response"
                trace["total_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
                return ProgressiveResult(
                    "conversation_response",
                    speech=str(llm_out["response_text"]),
                    trace=trace,
                )

            if mode == "repair_proposal" and llm_out.get("repairs"):
                repairs = llm_out["repairs"]
                ok, reason = validate_repair_proposal(
                    partial, repairs, authorized_targets=set(snapshot_by_id)
                )
                if ok:
                    wire_repairs = [
                        {
                            "clause_id": r["clause_id"],
                            "slot": r["slot"],
                            "value": r["value"],
                            "source": r["source"],
                            "evidence": list(r.get("evidence") or ()),
                        }
                        for r in repairs
                    ]
                    reviewed = await review_v5(
                        {"version": PROGRESSIVE_PROTOCOL_VERSION, "partial": partial, "repairs": wire_repairs}
                    )
                    trace["nlu_review"] = {
                        "accepted": reviewed.get("accepted"),
                        "reason": reviewed.get("reason"),
                        "requires_confirmation": reviewed.get("requires_confirmation"),
                    }
                    if reviewed.get("accepted"):
                        ops = tuple(reviewed.get("operations") or ())
                        needs_confirm = bool(reviewed.get("requires_confirmation", True)) or auto_resolve_level != "calibrated"
                        outcome_kind = "confirmation_required" if needs_confirm else "execute_plan"
                        trace["final_outcome"] = outcome_kind
                        trace["total_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
                        return ProgressiveResult(
                            outcome_kind,
                            operations=ops,
                            speech=_format_confirmation_prompt(list(ops), snapshot_by_id) if needs_confirm else None,
                            reason="llm_repair_reviewed",
                            trace=trace,
                        )
                else:
                    trace["nlu_review"] = {"accepted": False, "reason": reason}

            if mode in {"clarification", "refusal"}:
                resolved_mode = "clarification"
                trace["final_outcome"] = resolved_mode
                trace["total_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
                plan = llm_out.get("plan") if isinstance(llm_out.get("plan"), dict) else {}
                custom = None
                if mode == "clarification":
                    custom = llm_out.get("response_text") or llm_out.get("question") or plan.get("question")
                else:
                    candidate_q = llm_out.get("question") or plan.get("question")
                    if isinstance(candidate_q, str) and candidate_q.strip() and "restrição" not in candidate_q:
                        custom = candidate_q
                speech_text, eff_reason, q_kind, missing, cands = _build_slot_clarification(
                    partial,
                    v4_reason or (llm_out.get("reason") if mode == "clarification" else "llm_abstention"),
                    snapshot_by_id,
                    snapshot_payload,
                    custom_text=custom,
                )
                return ProgressiveResult(
                    resolved_mode,
                    speech=speech_text,
                    reason=eff_reason,
                    trace=trace,
                    question_kind=q_kind,
                    missing_slots=missing,
                    candidates=cands,
                )

        speech_text, eff_reason, q_kind, missing, cands = _build_slot_clarification(
            partial, v4_reason or "unresolved_progressive_pipeline", snapshot_by_id, snapshot_payload
        )
        trace["final_outcome"] = "clarification"
        trace["total_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
        return ProgressiveResult(
            "clarification",
            speech=speech_text,
            reason=eff_reason,
            trace=trace,
            question_kind=q_kind,
            missing_slots=missing,
            candidates=cands,
        )
