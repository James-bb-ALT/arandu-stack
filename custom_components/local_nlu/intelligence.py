"""Passive semantic fallback. HA contextual runtime remains the only executor."""
from __future__ import annotations

import asyncio
import logging
import time

from .ai_lab_core import digest, finite, validate_proposal
from .contextual_protocol import Operation
from .contextual_protocol import normalize

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
    threshold = max(.5, config.get("threshold", .5))
    minimum = max(.08, config.get("min_margin", .08))
    if (response.get("catalog_fingerprint") != digest(catalog)
            or not finite(confidence) or not threshold < confidence <= 1
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
    if proposal["requires_clarification"] or proposal["intent"] in (None, "conversation"):
        return True
    if len(proposal["target_entities"]) != 1:
        return False
    words = set(normalize(text).split()) - {"o", "a", "os", "as", "de", "da", "do", "na", "no", "e", "um", "uma"}
    if words & {"nao", "nunca", "exceto", "menos", "se", "quando"}:
        return False
    areas = {a["area_id"]: set(normalize(" ".join(a["names"])).split()) for a in catalog["areas"]}
    scores = {}
    for row in catalog["entities"]:
        if proposal["intent"] not in row["actions"] or row.get("attributes", {}).get("available") is not True:
            continue
        names = set(normalize(" ".join([row["name"], *row.get("aliases", [])])).split())
        room = areas.get(row.get("area_id"), set())
        evidence = words & (names - room - {"lugar", "dispositivo"})
        if evidence:
            scores[row["entity_id"]] = len(evidence) * 2 + len(words & room) + int(bool(context and context.get("origin_area") == row.get("area_id")))
    target = proposal["target_entities"][0]
    if target not in scores:
        return False
    best = max(scores.values())
    return scores[target] == best and sum(score == best for score in scores.values()) == 1


async def fallback(text, snapshot, options, request, context=None):
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
                body.update(model="e5-small", config={"threshold": .5, "min_margin": .08})
            else:
                body.update(llm_model="tucano", decision=decision)
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
                if not grounded_target(text, proposal, snapshot.payload, context):
                    trace[source]["status"] = "ambiguous"
                    continue
            if proposal:
                proposed = operations(proposal, snapshot, source)
                if proposed:
                    trace[source]["status"] = "proposed"
                    return proposed, trace
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            trace[source] = {"status": "error", "error": type(exc).__name__}
            _LOGGER.warning("ARANDU fallback stage=%s error=%s", source, type(exc).__name__)
        finally:
            trace[source]["latency_ms"] = (time.perf_counter() - before) * 1000
    return (), trace
