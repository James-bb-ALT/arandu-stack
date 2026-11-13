"""Universal progressive pipeline orchestrator: NLU v5 -> Decision VNext -> NLU Review -> LLM + Home Graph -> NLU Review."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import time
import uuid
from typing import Any, Awaitable, Callable

from .home_graph import HomeGraphCache, build_graph_context
from .progressive_contract import (
    PROGRESSIVE_PROTOCOL_VERSION,
    build_origin_context,
    validate_repair_proposal,
)


_AMBIGUITY_REASONS = {"ambiguous_area", "unknown_area", "ambiguous_target"}


def _ambiguity_clarification_speech(reason: str | None) -> str:
    if reason == "ambiguous_area":
        return "Há mais de um cômodo com esse nome. Em qual cômodo?"
    if reason == "unknown_area":
        return "Não encontrei esse cômodo. Em qual cômodo você quer executar?"
    if reason == "ambiguous_target":
        return "Encontrei mais de um dispositivo compatível. Qual deles você quer controlar?"
    return "Não tenho certeza da ação desejada. Pode confirmar o que quer fazer?"


@dataclass
class ProgressiveResult:
    outcome_kind: str  # "nlu_direct" | "safety_refusal" | "execute_plan" | "confirmation_required" | "clarification" | "conversation_response" | "refusal"
    operations: tuple[dict[str, Any], ...] = ()
    speech: str | None = None
    reason: str | None = None
    trace: dict[str, Any] = field(default_factory=dict)


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
                if v4_reason in _AMBIGUITY_REASONS:
                    trace["final_outcome"] = "clarification"
                    trace["total_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
                    return ProgressiveResult(
                        "clarification",
                        speech=_ambiguity_clarification_speech(v4_reason),
                        reason=v4_reason,
                        trace=trace,
                    )
                trace["final_outcome"] = "safety_refusal"
                trace["total_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
                return ProgressiveResult("safety_refusal", reason=decision_out.get("reason"), trace=trace)

            if d_status == "needs_clarification" and not (llm_enabled and llm_handoff is not None):
                trace["final_outcome"] = "clarification"
                trace["total_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
                clarify_reason = decision_out.get("reason") or v4_reason
                return ProgressiveResult(
                    "clarification",
                    speech=_ambiguity_clarification_speech(clarify_reason),
                    reason=clarify_reason,
                    trace=trace,
                )

            if d_status == "resolved_candidate" and decision_out.get("repairs"):
                repairs = decision_out["repairs"]
                ok, reason = validate_repair_proposal(
                    partial, repairs, authorized_targets=set(snapshot_by_id)
                )
                if ok:
                    reviewed = await review_v5(
                        {"version": PROGRESSIVE_PROTOCOL_VERSION, "partial": partial, "repairs": repairs}
                    )
                    trace["nlu_review"] = {
                        "accepted": reviewed.get("accepted"),
                        "reason": reviewed.get("reason"),
                        "requires_confirmation": reviewed.get("requires_confirmation"),
                    }
                    if reviewed.get("accepted"):
                        ops = tuple(reviewed.get("operations") or ())
                        needs_confirm = (
                            bool(reviewed.get("requires_confirmation"))
                            or bool(decision_out.get("requires_confirmation", True))
                            or not bool(decision_out.get("calibration_valid"))
                            or auto_resolve_level != "calibrated"
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
                    reviewed = await review_v5(
                        {"version": PROGRESSIVE_PROTOCOL_VERSION, "partial": partial, "repairs": repairs}
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
                resolved_mode = "clarification" if (mode == "refusal" and v4_reason in _AMBIGUITY_REASONS) else mode
                trace["final_outcome"] = resolved_mode
                trace["total_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
                plan = llm_out.get("plan") if isinstance(llm_out.get("plan"), dict) else {}
                speech_text = (
                    llm_out.get("response_text")
                    or llm_out.get("question")
                    or plan.get("question")
                    or (
                        _ambiguity_clarification_speech(v4_reason)
                        if v4_reason in _AMBIGUITY_REASONS
                        else "Não consegui confirmar uma ação segura para esse pedido."
                    )
                )
                return ProgressiveResult(
                    resolved_mode,
                    speech=speech_text,
                    reason=(
                        v4_reason
                        if resolved_mode == "clarification" and v4_reason in _AMBIGUITY_REASONS
                        else llm_out.get("reason")
                    ),
                    trace=trace,
                )

        trace["final_outcome"] = "clarification"
        trace["total_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
        return ProgressiveResult(
            "clarification",
            speech=(
                _ambiguity_clarification_speech(v4_reason)
                if v4_reason in _AMBIGUITY_REASONS
                else "Não entendi exatamente o que você quer fazer. Pode repetir ou especificar o dispositivo?"
            ),
            reason=v4_reason if v4_reason in _AMBIGUITY_REASONS else "unresolved_progressive_pipeline",
            trace=trace,
        )
