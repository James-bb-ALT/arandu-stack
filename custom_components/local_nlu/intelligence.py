"""Passive semantic fallback. HA contextual runtime remains the only executor."""
from __future__ import annotations

import asyncio
import logging
import time

from .ai_lab_core import digest, finite, validate_proposal
from .contextual_protocol import Operation
from .semantic_context import resolve_device, understood_action, hypothesis_allowed

_LOGGER = logging.getLogger(__name__)
TIMEOUT = 30


def eligible(outcome, *, pending=False):
    # Clarifications, permission failures, unsupported capabilities and stale
    # requests must never be reinterpreted as authorization by another model.
    return (not pending and (
        (outcome.status == "no_match" and outcome.reason in (None, "no_match", "unsupported_intent", "unknown_intent"))
        or (outcome.status == "clarification" and outcome.reason == "target_not_found"
            and not outcome.candidates and not outcome.options)))


def operations(proposal, snapshot, source):
    validate_proposal(proposal, snapshot.payload)
    if proposal["requires_clarification"] or proposal["intent"] in (None, "conversation"):
        return ()
    by_entity = {row["entity_id"]: key for key, row in snapshot.by_id.items()}
    return (Operation(source + "." + proposal["intent"], proposal["intent"],
                      tuple(sorted(by_entity[e] for e in proposal["target_entities"])), {},
                      evidence=("semantic_proposal",)),)


def accepted_decision(response, catalog):
    if response.get("decision_status") != "accepted":
        return None
    confidence, margin = response.get("confidence"), response.get("margin")
    config = response.get("applied_config") or {}
    threshold = max(.8, config.get("threshold", .8))
    minimum = max(.08, config.get("min_margin", .08))
    if (response.get("catalog_fingerprint") != digest(catalog)
            or not finite(confidence) or not threshold <= confidence <= 1
            or not finite(margin) or not minimum <= margin <= 2):
        raise ValueError("Aceitação sem calibração, catálogo ou margem válidos")
    proposal = validate_proposal(response.get("proposal"), catalog)
    top = response["ranking"][0]
    if (proposal["target_entities"] != [top["entity_id"]]
            or proposal["intent"] != top["intent"] or proposal["confidence"] != confidence):
        raise ValueError("Proposta diverge do ranking calibrado")
    return proposal


def grounded_target(text, proposal, catalog, context=None):
    """Independent conservative entity evidence; model ranking alone is insufficient.

    Production semantic fallback supports one explicit device. Ties remain a
    clarification even if the model chooses a top candidate.
    """
    if proposal["intent"] in (None, "conversation"):
        return False
    if len(proposal["target_entities"]) != 1:
        return False
    row = resolve_device(text, catalog, context, action=proposal["intent"])
    return (row is not None and row["entity_id"] == proposal["target_entities"][0]
            and proposal["intent"] in row["actions"])


def proposal_route(text, proposal, catalog, context=None, force_review=False, auto_decision=False):
    if not grounded_target(text, proposal, catalog, context) or not hypothesis_allowed(text):
        return "clarification"
    known = understood_action(text)
    if known is not None and known != proposal["intent"]:
        return "clarification"
    if auto_decision:
        return "proposed"
    if known is None or proposal["requires_clarification"] or force_review:
        return "confirmation_required"
    return "proposed"


def nlu_summary(outcome):
    """Only bounded semantic hints; never forward diagnostics or operations."""
    summary = {'status': outcome.status}
    if outcome.reason:
        summary['reason'] = outcome.reason[:128]
    command = outcome.command or {}
    for key in ('action', 'mention', 'area'):
        value = command.get(key)
        if isinstance(value, str) and value:
            summary[key] = value[:128]
    return summary


async def fallback(text, snapshot, options, request, context=None, nlu=None):
    """Snapshot policy per request; bounded calls; no effects or late callbacks."""
    trace = {}
    decision = None
    for source in ("decision", "llm"):
        if options.get(source + "_enabled", False) is not True:
            trace[source] = {"status": "disabled"}
            continue
        before = time.perf_counter()
        trace[source] = {"status": "started"}
        try:
            body = {"action": source, "text": text, "catalog": snapshot.payload, "context": context or {}}
            if source == "decision":
                body.update(model="e5-small", config={"threshold": .8, "min_margin": .08})
            else:
                body.update(llm_model="tucano", decision=decision, nlu=nlu or {})
            async with asyncio.timeout(TIMEOUT):
                response = await request(body)
            trace[source] = {"status": "responded"}
            if source == "decision":
                decision = response
                proposal = accepted_decision(response, snapshot.payload)
                trace[source]["status"] = response.get("decision_status", "rejected")
            else:
                retrieval = response.get("retrieval") or decision or {}
                allowed = {r["entity_id"] for r in retrieval.get("ranking", [])}
                proposal = validate_proposal(response.get("proposal"), snapshot.payload, allowed)
                # A model's self-reported confidence is never an authorization signal.
                proposal["confidence"] = None
            if proposal:
                route = proposal_route(text, proposal, snapshot.payload, context, response.get("confirmation_required") is True,
                                       auto_decision=source == 'decision')
                trace[source]["status"] = route
                if source == 'decision':
                    trace[source].update(confidence=response['confidence'], auto_corrected=understood_action(text) is None)
                if route == "clarification":
                    continue
                if route == "confirmation_required" or source == 'decision':
                    proposal = {**proposal, "requires_clarification": False, "clarification_question": None}
                proposed = operations(proposal, snapshot, source)
                if proposed:
                    return proposed, trace
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            trace[source] = {"status": "error", "error": type(exc).__name__}
            _LOGGER.warning("ARANDU fallback stage=%s error=%s", source, type(exc).__name__)
        finally:
            trace[source]["latency_ms"] = (time.perf_counter() - before) * 1000
    return (), trace
