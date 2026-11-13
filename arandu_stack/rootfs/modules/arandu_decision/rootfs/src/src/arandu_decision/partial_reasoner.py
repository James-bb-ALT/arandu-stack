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
            alternatives.extend(
                {"slot": "target", "value": c, "score": None}
                for c in (target_slot.get("candidates") or ())
            )

        if "action" in unresolved and (slots.get("action") or {}).get("status") in {"unknown", "candidate"}:
            allowed_actions = list(clause.get("allowed_actions") or ())
            if not allowed_actions:
                continue
            ranked = rank_action_candidates(clause.get("original_text", ""), allowed_actions, encoder=encoder)
            if not ranked or ranked[0]["score"] < 0.65:
                alternatives.extend({"slot": "action", "value": r["value"], "score": r["score"]} for r in ranked[:3])
                continue
            top = ranked[0]
            margin = round(top["score"] - ranked[1]["score"], 4) if len(ranked) > 1 else 1.0
            margins.append(margin)
            if margin < 0.08:
                alternatives.extend({"slot": "action", "value": r["value"], "score": r["score"]} for r in ranked[:3])
                continue
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

        # Area disambiguation via satellite origin_area when ambiguous
        area_slot = slots.get("area") or {}
        origin_area = (partial.get("origin") or {}).get("area_id")
        if area_slot.get("status") in {"conflicting", "candidate", "unknown"}:
            area_cands = list(area_slot.get("candidates") or ())
            if origin_area and origin_area in area_cands:
                repairs.append(
                    {
                        "clause_id": cid,
                        "slot": "area",
                        "value": origin_area,
                        "source": "origin_context",
                        "evidence": ["origin_area_match"],
                        "raw_score": 0.95,
                    }
                )
            elif len(area_cands) > 1:
                alternatives.extend({"slot": "area", "value": c, "score": None} for c in area_cands[:4])

        # Multi-slot repair: if target is unknown/candidate (not conflicting) and single candidate exists
        if "target" in unresolved and target_slot.get("status") in {"unknown", "candidate"}:
            cands = list(target_slot.get("candidates") or ())
            if len(cands) == 1:
                repairs.append(
                    {
                        "clause_id": cid,
                        "slot": "target",
                        "value": cands[0],
                        "source": "decision_ranked",
                        "evidence": ["single_authorized_candidate"],
                        "raw_score": 0.9,
                    }
                )
            elif len(cands) > 1:
                alternatives.extend({"slot": "target", "value": c, "score": None} for c in cands[:4])

    if not repairs:
        v4_reason = (partial.get("v4_outcome") or {}).get("reason")
        if v4_reason in {"ambiguous_area", "unknown_area", "ambiguous_target"}:
            return {
                "decision_contract": DECISION_PARTIAL_CONTRACT,
                "route": route,
                "status": "needs_clarification",
                "repairs": [],
                "alternatives": alternatives[:4],
                "confidence": None,
                "margin": None,
                "calibration_valid": False,
                "requires_confirmation": False,
                "reason": v4_reason,
            }
        return {
            "decision_contract": DECISION_PARTIAL_CONTRACT,
            "route": route,
            "status": "abstain",
            "repairs": [],
            "alternatives": alternatives[:4],
            "confidence": None,
            "margin": None,
            "calibration_valid": False,
            "requires_confirmation": False,
            "reason": "ambiguous_target" if any(a.get("slot") == "target" for a in alternatives) else "no_repair_produced",
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
