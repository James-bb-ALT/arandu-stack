"""Decision VNext partial gap-filler (`decision_partial_v1`): repairs ONLY unresolved slots."""
from __future__ import annotations

from typing import Any

from .phonetics_ptbr import cheap_triage, rank_action_candidates

DECISION_PARTIAL_CONTRACT = 1


def reason_partial(payload: dict[str, Any], *, encoder=None) -> dict[str, Any]:
    """Resolve only unresolved/candidate slots from a PartialUnderstanding. Never executes HA."""
    partial = payload.get("partial_understanding") if "partial_understanding" in payload else payload
    if not isinstance(partial, dict):
        return {
            "decision_contract": DECISION_PARTIAL_CONTRACT,
            "route": "unknown",
            "status": "abstain",
            "repairs": [],
            "alternatives": [],
            "confidence": None,
            "margin": None,
            "calibration_valid": False,
            "requires_confirmation": False,
            "reason": "invalid_partial_payload",
        }

    text = " ".join(c.get("original_text", "") for c in partial.get("clauses") or ())
    route = cheap_triage(text, partial)
    if route == "denied":
        return {
            "decision_contract": DECISION_PARTIAL_CONTRACT,
            "route": "denied",
            "status": "denied",
            "repairs": [],
            "alternatives": [],
            "confidence": None,
            "margin": None,
            "calibration_valid": False,
            "requires_confirmation": False,
            "reason": "safety_or_constraint_locked",
        }
    if route == "general_conversation":
        return {
            "decision_contract": DECISION_PARTIAL_CONTRACT,
            "route": "general_conversation",
            "status": "general_conversation",
            "repairs": [],
            "alternatives": [],
            "confidence": None,
            "margin": None,
            "calibration_valid": False,
            "requires_confirmation": False,
            "reason": "general_conversation",
        }

    repairs: list[dict[str, Any]] = []
    alternatives: list[dict[str, Any]] = []
    margins: list[float] = []

    for clause in partial.get("clauses") or ():
        cid = clause.get("clause_id", 0)
        slots = clause.get("slots") or {}
        unresolved = list(clause.get("unresolved") or ())

        target_slot = slots.get("target") or {}
        if target_slot.get("status") == "conflicting":
            return {
                "decision_contract": DECISION_PARTIAL_CONTRACT,
                "route": "possible_action",
                "status": "needs_clarification",
                "repairs": [],
                "alternatives": [{"value": c, "score": None} for c in target_slot.get("candidates") or ()],
                "confidence": None,
                "margin": 0.0,
                "calibration_valid": False,
                "requires_confirmation": True,
                "reason": "ambiguous_target",
            }

        if "action" in unresolved and (slots.get("action") or {}).get("status") in {"unknown", "candidate"}:
            allowed_actions = list(clause.get("allowed_actions") or ())
            if not allowed_actions:
                return {
                    "decision_contract": DECISION_PARTIAL_CONTRACT,
                    "route": route,
                    "status": "abstain",
                    "repairs": [],
                    "alternatives": [],
                    "confidence": None,
                    "margin": None,
                    "calibration_valid": False,
                    "requires_confirmation": False,
                    "reason": "no_allowed_actions",
                }
            ranked = rank_action_candidates(clause.get("original_text", ""), allowed_actions, encoder=encoder)
            if not ranked or ranked[0]["score"] < 0.65:
                return {
                    "decision_contract": DECISION_PARTIAL_CONTRACT,
                    "route": route,
                    "status": "needs_clarification",
                    "repairs": [],
                    "alternatives": [{"value": r["value"], "score": r["score"]} for r in ranked[:3]],
                    "confidence": None,
                    "margin": None,
                    "calibration_valid": False,
                    "requires_confirmation": True,
                    "reason": "no_confident_action_match",
                }
            top = ranked[0]
            margin = round(top["score"] - ranked[1]["score"], 4) if len(ranked) > 1 else 1.0
            margins.append(margin)
            if margin < 0.08:
                return {
                    "decision_contract": DECISION_PARTIAL_CONTRACT,
                    "route": "possible_action",
                    "status": "needs_clarification",
                    "repairs": [],
                    "alternatives": [{"value": r["value"], "score": r["score"]} for r in ranked[:3]],
                    "confidence": None,
                    "margin": margin,
                    "calibration_valid": False,
                    "requires_confirmation": True,
                    "reason": "insufficient_margin",
                }
            evidence = list(top.get("evidence") or [])
            if "capability_compatible" not in evidence:
                evidence.append("capability_compatible")
            repairs.append(
                {
                    "clause_id": cid,
                    "slot": "action",
                    "value": top["value"],
                    "source": "decision_ranked",
                    "evidence": evidence,
                    "raw_score": top["score"],
                }
            )
            alternatives.extend({"value": r["value"], "score": r["score"]} for r in ranked[1:4])

    if not repairs:
        return {
            "decision_contract": DECISION_PARTIAL_CONTRACT,
            "route": route,
            "status": "abstain",
            "repairs": [],
            "alternatives": [],
            "confidence": None,
            "margin": None,
            "calibration_valid": False,
            "requires_confirmation": False,
            "reason": "no_repair_produced",
        }

    return {
        "decision_contract": DECISION_PARTIAL_CONTRACT,
        "route": "possible_action",
        "status": "resolved_candidate",
        "repairs": repairs,
        "alternatives": alternatives[:4],
        "confidence": None,
        "margin": min(margins) if margins else None,
        "calibration_valid": False,
        "requires_confirmation": True,
        "reason": "uncalibrated_asr_repair",
    }
