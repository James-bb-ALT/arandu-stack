"""Passive semantic fallback. HA contextual runtime remains the only executor."""
from __future__ import annotations

import asyncio
import logging
import time

from .ai_lab_core import digest, finite, validate_proposal
from .contextual_protocol import Operation
from .semantic_context import resolve_device, understood_action, hypothesis_allowed, conversation_allowed, clearly_conversational, semantic_refusal, read_only_question, speech_only_request
from .request_trace import CURRENT

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


def accepted_decision(response, catalog, text=None, context=None):
    status = response.get('decision_status')
    structured = response.get('algorithm') == 'structured-v3'
    if response.get('algorithm') not in (None, 'baseline-v2', 'structured-v3'):
        raise ValueError('Algoritmo Decision desconhecido')
    if structured:
        from .decision_context import fields, semantic_fingerprint, grounded
        if (text is None or response.get('contract_version') != 2
                or response.get('semantic_fingerprint') != semantic_fingerprint(catalog)):
            raise ValueError('Contrato semântico incompatível')
        evidence = fields(text, catalog, context)
        if status in {'resolved','accepted','confirmation_required'}:
            p = validate_proposal(response.get('proposal'), catalog)
            if not grounded(text, p, catalog, context):
                raise ValueError('Campos sem fundamento independente')
            if status == 'resolved' and (response.get('grounding') != 'deterministic-v2'
                    or evidence['action']['exact'] != p['intent']
                    or evidence['device']['evidence'] == 'spelling_name'
                    or p['requires_clarification'] or p['confidence'] is not None):
                raise ValueError('Resolução estruturada inconsistente')
            if status == 'accepted' and (response.get('calibration_valid') is not True
                    or evidence['device']['evidence'] == 'spelling_name'):
                raise ValueError('Aceitação estruturada sem calibração válida')
        if status == 'resolved':
            if response.get('catalog_fingerprint') != digest(catalog):
                raise ValueError('Catálogo alterado')
            return p
    if status in {'resolved', 'confirmation_required'}:
        if text is None or response.get('catalog_fingerprint') != digest(catalog):
            raise ValueError('Decisão sem catálogo ou texto verificáveis')
        proposal = validate_proposal(response.get('proposal'), catalog)
        if not grounded_target(text, proposal, catalog, context) or not hypothesis_allowed(text, catalog):
            raise ValueError('Decisão sem alvo inequívoco')
        if status == 'resolved':
            if (response.get('grounding') != 'deterministic-v1' or understood_action(text, catalog) != proposal['intent']
                    or proposal['requires_clarification'] or proposal['confidence'] is not None):
                raise ValueError('Resolução determinística inconsistente')
        elif not proposal['requires_clarification'] or response.get('confirmation_required') is not True:
            raise ValueError('Hipótese deve exigir confirmação')
        return proposal
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


def typed_operations(response, text, snapshot, context=None):
    """Rebuild an LLM v2 plan independently inside the HA trust boundary."""
    from .automation_contract import validate
    from .contextual_protocol import validate_parameters
    if response.get('contract_version') != 2 or response.get('catalog_fingerprint') != digest(snapshot.payload):
        raise ValueError('Contrato/catálogo LLM v2 incompatível')
    plan=validate(response.get('plan'),text,snapshot.payload,context)
    if plan['type']!='automation':return ()
    by_entity={row['entity_id']:key for key,row in snapshot.by_id.items()}
    return tuple(Operation('llm2.'+op['action'],op['action'],(by_entity[op['entity_id']],),
        validate_parameters(op['parameters']),evidence=('llm_v2_grounded',)) for op in plan['operations'])


def grounded_target(text, proposal, catalog, context=None):
    """Independent conservative entity evidence; model ranking alone is insufficient.

    Production semantic fallback supports one explicit device. Ties remain a
    clarification even if the model chooses a top candidate.
    """
    if proposal["intent"] in (None, "conversation"):
        return False
    if len(proposal["target_entities"]) != 1:
        return False
    from .decision_context import fields, grounded
    field_result = fields(text, catalog, context)
    if field_result['refusal']:
        return False
    if field_result['device']['unambiguous']:
        return grounded(text, proposal, catalog, context)
    row = resolve_device(text, catalog, context, action=proposal["intent"])
    return (row is not None and row["entity_id"] == proposal["target_entities"][0]
            and proposal["intent"] in row["actions"])


def proposal_route(text, proposal, catalog, context=None, force_review=False, auto_decision=False):
    if proposal['intent'] == 'conversation' and conversation_allowed(text, catalog) and not proposal['requires_clarification']:
        return 'conversation'
    if speech_only_request(text) or (read_only_question(text) and proposal['intent'] != 'query'):
        return 'clarification'
    if not grounded_target(text, proposal, catalog, context) or not hypothesis_allowed(text, catalog):
        return "clarification"
    known = understood_action(text, catalog)
    from .decision_context import fields
    evidence = fields(text, catalog, context)
    known = evidence['action']['exact'] or known
    if known is not None and known != proposal["intent"]:
        return "clarification"
    if auto_decision:
        if evidence['device']['evidence'] == 'spelling_name':
            return 'confirmation_required'
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
    refusal = semantic_refusal(text, snapshot.payload)
    if refusal or (nlu and not eligible_summary(nlu)):
        return (), {'safety': {'status': 'rejected', 'reason': refusal or 'nlu_not_eligible'}}
    trace = {}
    decision = None
    for source in ("decision", "llm"):
        if source == 'decision' and options.get('llm_enabled') is True and clearly_conversational(text, snapshot.payload):
            trace[source] = {'status': 'skipped', 'reason': 'general_conversation'}
            continue
        if options.get(source + "_enabled", False) is not True:
            trace[source] = {"status": "disabled"}
            continue
        before = time.perf_counter()
        recorder = CURRENT.get()
        reason, confidence = None, None
        if recorder:
            recorder.start(source)
        trace[source] = {"status": "started"}
        try:
            body = {"action": source, "text": text, "catalog": snapshot.payload, "context": context or {}}
            if source == "decision":
                body.update(config={"threshold": .8, "min_margin": .08})
            else:
                body.update(decision=llm_hint(decision), nlu=nlu or {}, accept_contracts=[1,2])
            async with asyncio.timeout(TIMEOUT):
                response = await request(body)
            reason = response.get('reason')
            confidence = response.get('confidence') if source == 'decision' else None
            trace[source] = {"status": "responded"}
            if source == "decision":
                decision = response
                proposal = accepted_decision(response, snapshot.payload, text, context)
                trace[source]["status"] = response.get("decision_status", "rejected")
                if (response.get('algorithm') == 'structured-v3' and response.get('decision_status') == 'ambiguous'
                        and (response.get('analysis') or {}).get('device', {}).get('candidate_count', 0) > 1):
                    trace['llm'] = {'status':'skipped','reason':'true_entity_ambiguity'}
                    return (), trace
            else:
                if response.get('contract_version') == 2 and response.get('kind') in {'automation','clarification'}:
                    proposed=typed_operations(response,text,snapshot,context) if response.get('kind')=='automation' else ()
                    trace[source].update(status='proposed' if proposed else 'clarification',
                        question=(response.get('plan') or {}).get('question'),contract_version=2)
                    return proposed,trace
                retrieval = response.get("retrieval") or decision or {}
                allowed = {r["entity_id"] for r in retrieval.get("ranking", [])}
                proposal = validate_proposal(response.get("proposal"), snapshot.payload, allowed)
                reply = response.get('response_text')
                if (proposal['intent'] == 'conversation' and not proposal['requires_clarification']
                        and not proposal['target_entities'] and proposal['target_area'] is None
                        and conversation_allowed(text, snapshot.payload) and type(reply) is str and 1 <= len(reply.strip()) <= 1000):
                    trace[source].update(status='conversation', response_text=reply.strip())
                    return (), trace
                # A model's self-reported confidence is never an authorization signal.
                proposal["confidence"] = None
            if proposal:
                route = proposal_route(text, proposal, snapshot.payload, context, response.get("confirmation_required") is True,
                                       auto_decision=source == 'decision' and response.get('decision_status') in {'accepted', 'resolved'})
                trace[source]["status"] = route
                if source == 'decision':
                    trace[source].update(confidence=response.get('confidence'), auto_corrected=understood_action(text, snapshot.payload) is None)
                if route == "clarification":
                    continue
                if route == "confirmation_required" or source == 'decision':
                    proposal = {**proposal, "requires_clarification": False, "clarification_question": None}
                proposed = operations(proposal, snapshot, source)
                if proposed:
                    return proposed, trace
        except asyncio.CancelledError:
            trace[source] = {'status': 'cancelled'}
            raise
        except Exception as exc:
            confidence = None
            trace[source] = {"status": "error", "error": type(exc).__name__}
            _LOGGER.warning("ARANDU fallback stage=%s error=%s", source, type(exc).__name__)
        finally:
            trace[source]["latency_ms"] = (time.perf_counter() - before) * 1000
            if recorder:
                row = trace[source]
                status = {'proposed': 'accepted', 'clarification': 'rejected', 'responded': 'rejected', 'conversation': 'completed'}.get(row['status'], row['status'])
                recorder.end(source, status, row.get('error') or reason, confidence)
    return (), trace


def eligible_summary(summary):
    """A caller cannot reinterpret an explicit NLU refusal as a neural request."""
    return (summary.get('status') == 'no_match' and summary.get('reason') in
            (None, 'no_match', 'unsupported_intent', 'unknown_intent')) or (
            summary.get('status') == 'clarification' and summary.get('reason') == 'target_not_found')


def llm_hint(decision):
    """A rejected compact v3 ranking must not truncate the LLM search universe."""
    if decision and decision.get('algorithm') == 'structured-v3':
        return {'ranking':[], 'analysis':decision.get('analysis'), 'reason':decision.get('reason'),
                'decision_status':decision.get('decision_status'), 'retrieval_required':True}
    return decision
