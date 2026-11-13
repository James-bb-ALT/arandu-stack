"""Pure NLU -> Decision -> LLM fallback routing policy.

No Home Assistant, HTTP, model, or I/O dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class RouteCategory(str, Enum):
    NLU_RESOLVED = "NLU_RESOLVED"
    RECOVERABLE = "RECOVERABLE"
    CLARIFY = "CLARIFY"
    SAFETY_BLOCK = "SAFETY_BLOCK"
    PENDING_DIALOGUE = "PENDING_DIALOGUE"
    UNSUPPORTED = "UNSUPPORTED"


SAFETY_REASONS = frozenset(
    {
        "stale",
        "permission_denied",
        "denied",
        "contradictory_plan",
        "semantic_negation",
        "semantic_condition",
        "unsupported_exclusion",
        "prompt_injection",
        "sensitive_domain",
        "untrusted_inventory",
        "expired_confirmation",
        "bulk_scope_missing",
        "bulk_target_set_changed",
        "origin_area_changed",
        "semantic_catalog_changed",
    }
)

CLARIFY_REASONS = frozenset(
    {
        "ambiguous_target",
        "unknown_area",
        "missing_origin_area",
        "ambiguous_area",
        "missing_slot",
    }
)

RECOVERABLE_NO_MATCH_REASONS = frozenset(
    {
        None,
        "no_match",
        "unknown_intent",
        "unsupported_intent",
    }
)


@dataclass(frozen=True)
class FallbackRoute:
    category: RouteCategory
    decision_allowed: bool
    llm_allowed: bool
    execution_allowed: bool
    clarification_only: bool
    reason: str
    preserve_slots: dict[str, Any]
    missing_slots: tuple[str, ...] = ()
    escalation_blocked_by: str | None = None
    safety_rule: str | None = None


def _extract_preserved_slots(command: dict | None) -> dict[str, Any]:
    if not isinstance(command, dict):
        return {}
    slots: dict[str, Any] = {}
    for key in ("action", "intent", "domain", "area", "mention", "scope", "metric"):
        val = command.get(key)
        if isinstance(val, str) and val.strip():
            slots[key] = val.strip()
    return slots


def _missing_slots_for(slots: dict[str, Any], reason: str | None) -> tuple[str, ...]:
    missing: list[str] = []
    if reason in ("unknown_intent", "unsupported_intent", "no_match") and "action" not in slots and "intent" not in slots:
        missing.append("action")
    if reason in ("target_not_found", "ambiguous_target", "missing_slot"):
        missing.append("target")
    if reason in ("unknown_area", "missing_origin_area", "ambiguous_area") and "area" not in slots:
        missing.append("area")
    if reason == "no_compatible_capability":
        missing.append("capability")
    return tuple(missing)


def classify_fallback(
    *,
    nlu_status: str,
    reason: str | None,
    command: dict | None,
    candidates: list | tuple,
    options: list | tuple,
    pending: bool,
    safety_flags: list | tuple = (),
    explicitly_denied: bool = False,
    user_scope_known: bool = True,
) -> FallbackRoute:
    """Classify the exact routing decision after NLU evaluation."""
    slots = _extract_preserved_slots(command)
    missing = _missing_slots_for(slots, reason)

    # 1. Absolute precedence: security / permission / negative constraints / untrusted scope
    if explicitly_denied or safety_flags or not user_scope_known or reason in SAFETY_REASONS:
        rule = (
            (list(safety_flags)[0] if safety_flags else None)
            or (reason if reason in SAFETY_REASONS else None)
            or ("user_scope_unknown" if not user_scope_known else "explicit_denial")
        )
        return FallbackRoute(
            category=RouteCategory.SAFETY_BLOCK,
            decision_allowed=False,
            llm_allowed=False,
            execution_allowed=False,
            clarification_only=False,
            reason=reason or rule,
            preserve_slots=slots,
            missing_slots=missing,
            escalation_blocked_by="safety_policy",
            safety_rule=rule,
        )

    # 2. Active pending dialogue / confirmation state machine
    if pending and nlu_status != "plan":
        return FallbackRoute(
            category=RouteCategory.PENDING_DIALOGUE,
            decision_allowed=False,
            llm_allowed=False,
            execution_allowed=False,
            clarification_only=True,
            reason=reason or "pending_dialogue",
            preserve_slots=slots,
            missing_slots=missing,
            escalation_blocked_by="pending_dialogue",
        )

    # 3. Already resolved deterministically by NLU
    if nlu_status == "plan":
        return FallbackRoute(
            category=RouteCategory.NLU_RESOLVED,
            decision_allowed=False,
            llm_allowed=False,
            execution_allowed=True,
            clarification_only=False,
            reason=reason or "resolved",
            preserve_slots=slots,
            missing_slots=(),
        )

    # 4. Clarification states
    if nlu_status == "clarification":
        if reason == "target_not_found":
            return FallbackRoute(
                category=RouteCategory.RECOVERABLE,
                decision_allowed=True,
                llm_allowed=True,
                execution_allowed=True,
                clarification_only=False,
                reason="target_not_found",
                preserve_slots=slots,
                missing_slots=missing,
            )

        if reason == "no_compatible_capability":
            action = slots.get("action") or slots.get("intent") or ""
            domain = slots.get("domain") or ""
            is_query_or_typed = action.endswith(".query") or action == "query" or bool(domain) or bool(action)
            if is_query_or_typed:
                return FallbackRoute(
                    category=RouteCategory.RECOVERABLE,
                    decision_allowed=True,
                    llm_allowed=True,
                    execution_allowed=True,
                    clarification_only=False,
                    reason="no_compatible_capability",
                    preserve_slots=slots,
                    missing_slots=missing,
                )
            return FallbackRoute(
                category=RouteCategory.UNSUPPORTED,
                decision_allowed=False,
                llm_allowed=False,
                execution_allowed=False,
                clarification_only=False,
                reason="no_compatible_capability",
                preserve_slots=slots,
                missing_slots=missing,
                escalation_blocked_by="unsupported_capability",
            )

        if reason in CLARIFY_REASONS:
            if reason in ("ambiguous_target", "missing_slot"):
                return FallbackRoute(
                    category=RouteCategory.RECOVERABLE,
                    decision_allowed=True,
                    llm_allowed=True,
                    execution_allowed=True,
                    clarification_only=False,
                    reason=reason,
                    preserve_slots=slots,
                    missing_slots=missing,
                )
            if reason == "missing_origin_area" and (
                slots.get("scope") in ("global", "bulk_global") or slots.get("area") in ("casa", "global")
            ):
                return FallbackRoute(
                    category=RouteCategory.RECOVERABLE,
                    decision_allowed=True,
                    llm_allowed=True,
                    execution_allowed=True,
                    clarification_only=False,
                    reason=reason,
                    preserve_slots=slots,
                    missing_slots=missing,
                )
            return FallbackRoute(
                category=RouteCategory.CLARIFY,
                decision_allowed=True,
                llm_allowed=True,
                execution_allowed=False,
                clarification_only=True,
                reason=reason,
                preserve_slots=slots,
                missing_slots=missing,
                escalation_blocked_by="clarification_required",
            )

    # 5. Recoverable no_match / unavailable
    if nlu_status in ("no_match", "unavailable") and reason in (*RECOVERABLE_NO_MATCH_REASONS, "target_not_found"):
        return FallbackRoute(
            category=RouteCategory.RECOVERABLE,
            decision_allowed=True,
            llm_allowed=True,
            execution_allowed=True,
            clarification_only=False,
            reason=reason or nlu_status or "no_match",
            preserve_slots=slots,
            missing_slots=missing,
        )

    # 6. Default unsupported
    return FallbackRoute(
        category=RouteCategory.UNSUPPORTED,
        decision_allowed=False,
        llm_allowed=False,
        execution_allowed=False,
        clarification_only=False,
        reason=reason or nlu_status or "unsupported",
        preserve_slots=slots,
        missing_slots=missing,
        escalation_blocked_by="unsupported_status",
    )


def route_diagnostic(
    route: FallbackRoute,
    *,
    decision_called: bool = False,
    llm_called: bool = False,
) -> dict[str, Any]:
    """Read-only stage diagnostic without storing user transcripts."""
    return {
        "status": route.category.value,
        "route": route.category.value,
        "reason": route.reason,
        "preserved_slots": dict(route.preserve_slots),
        "missing_slots": list(route.missing_slots),
        "escalation_blocked_by": route.escalation_blocked_by,
        "safety_rule": route.safety_rule,
        "decision_called": bool(decision_called),
        "llm_called": bool(llm_called),
    }
