"""Passive AI Lab contracts. No HA credentials, effects, or model downloads."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from collections import Counter

VERSION = "arandu-joint-v1"
ACTION_TEXT = {"turn_on": "Ligar acender ativar", "turn_off": "Desligar apagar desativar", "query": "Consultar qual é o estado a temperatura"}
MODELS = {
    'minilm-multilingual':{'name':'Multilingual MiniLM L12','repo':'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2','license':'Apache-2.0','kind':'decision','runtime':'CPU / OpenVINO Intel GPU'},
    'e5-base':{'name':'Multilingual E5 Base','repo':'intfloat/multilingual-e5-base','license':'MIT','kind':'decision','runtime':'CPU / OpenVINO Intel GPU'},
    'qwen3': {'name':'Qwen3 0.6B Q8_0 · experimental','repo':'Qwen/Qwen3-0.6B-GGUF','license':'Apache-2.0','kind':'llm','runtime':'llama.cpp / CPU'},
    "brazembed": {"name": "BrazEmbed PT-BR 110M", "repo": "tardellirs/brazembed-pt-br", "license": "MIT", "runtime": "sentence-transformers / CPU", "kind": "decision"},
    "e5-small": {"name": "Multilingual E5 Small", "repo": "intfloat/multilingual-e5-small", "license": "MIT", "runtime": "sentence-transformers / CPU; query/passsage prefixes", "kind": "decision"},
    "saracura": {"name": "Saracura PT-BR 322M", "repo": "felhen-ai/saracura-ptbr-v0", "license": "Apache-2.0", "kind": "decision", "runtime": "Laya / CPU", "github": "https://github.com/felhen-ai/saracura"},
    "jatoba": {"name": "JATOBÁ Decision 169M · não comercial", "repo": "leoabreu288/jatoba-decision", "license": "CC-BY-NC-SA-4.0 (pesos); Apache-2.0 (código)", "kind": "decision", "runtime": "jatoba / CPU", "github": "https://github.com/LeonardoVilela/jatoba-decision", "noncommercial": True},
    "qwen": {"name": "Qwen3.5 0.8B Q4_0 · catálogo PC", "repo": "ggml-org/Qwen3.5-0.8B-GGUF", "license": "Apache-2.0", "kind": "llm", "file": "Qwen3.5-0.8B-Q4_0.gguf", "runtime": "llama.cpp / CPU", 'unavailable':'Catálogo histórico do Lab PC; não validado pelo backend deste app nesta release.'},
    "tucano": {"name": "Tucano 2 Qwen 0.5B Instruct Q4_K_M", "repo": "2DTi/tucano2-0.5b-instruct-gguf", "source_repo": "Polygl0t/Tucano2-qwen-0.5B-Instruct", "license": "Apache-2.0", "kind": "llm", "file": "tucano2-0.5b-instruct-Q4_K_M.gguf", "runtime": "llama.cpp / CPU", "github": "https://github.com/Polygl0t/llm-foundry"},
}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def decision_matrix(rows,examples,catalog):
    """Labelled diagnostics only. Warm-ups excluded; no cosine-as-confidence."""
    import statistics
    metrics={};by_entity={e['entity_id']:e for e in catalog['entities']}
    for row in rows:
        key=row['combination']
        cell=metrics.setdefault(key,{'samples':0,'labelled':0,'plan_labelled':0,'correct_plan':0,'intent_correct':0,'entity_correct':0,
            'intent_labelled':0,'entity_labelled':0,'area_labelled':0,'abstention_labelled':0,
            'area_correct':0,'abstention_correct':0,'false_activations':0,'confirmations':0,'errors':0,'latencies':[],
            'embedding_requests':0,'deterministic_requests':0,'stage_values':{},
            'peak_rss_bytes':None,'status':'pass','cold_start_ms':None})
        if finite(row.get('cold_start_ms')):cell['cold_start_ms']=max(cell['cold_start_ms'] or 0,row['cold_start_ms'])
        if row.get('warmup'):continue
        if row.get('decision_status') in {'unsupported','error'}:
            cell.update(status=row['decision_status'],reason=row.get('reason'));cell['errors']+=1;continue
        cell['samples']+=1;cell['latencies'].append(row.get('wall_ms',row.get('total_ms',0)))
        cell['embedding_requests']+=int(row.get('embedding_used') is True)
        cell['deterministic_requests']+=int(row.get('embedding_used') is False)
        for name in ['tokenization_ms','embedding_ms','ranking_ms','cpu_ms','retrieval_ms']:
            if finite(row.get(name)):cell['stage_values'].setdefault(name,[]).append(row[name])
        if isinstance(row.get('memory_bytes'),int):cell['peak_rss_bytes']=max(cell['peak_rss_bytes'] or 0,row['memory_bytes'])
        label=examples[row['case']].get('expected');p=row.get('proposal') or {};targets=p.get('target_entities',[])
        areas={by_entity[e].get('area_id') for e in targets if e in by_entity}
        actual={'intent':p.get('intent'),'target_entities':targets,'target_area':next(iter(areas)) if len(areas)==1 else None,
            'requires_clarification':not bool(p) or p.get('requires_clarification') is True}
        cell['confirmations']+=int(row.get('confirmation_required') is True)
        if label is not None:
            cell['labelled']+=1
            whole=set(label)>={'intent','target_entities','target_area','requires_clarification'} or label.get('requires_clarification') is True
            cell['plan_labelled']+=int(whole)
            cell['correct_plan']+=int(whole and all(actual[k]==v for k,v in label.items()) and (label.get('requires_clarification') is not True or not targets))
            for field,score in [('intent','intent_correct'),('target_entities','entity_correct'),('target_area','area_correct')]:
                if field in label:
                    cell[score]+=int(actual[field]==label[field]);cell[score.replace('_correct','_labelled')]+=1
            cell['abstention_labelled']+=int(label.get('requires_clarification') is True)
            cell['abstention_correct']+=int(label.get('requires_clarification') is True and actual['requires_clarification'])
            cell['false_activations']+=int(label.get('requires_clarification') is True and bool(targets) and not actual['requires_clarification'])
    for cell in metrics.values():
        latencies=sorted(cell.pop('latencies'))
        cell['latency_ms']={f'p{p}':latencies[min(len(latencies)-1,round((len(latencies)-1)*p/100))] for p in [50,95,99]} if latencies else {}
        cell['stage_latency']={name:{'samples':len(values),**{f'p{p}':sorted(values)[min(len(values)-1,round((len(values)-1)*p/100))] for p in [50,95,99]}}
            for name,values in cell.pop('stage_values').items()}
        cell['throughput_per_second']=1000*len(latencies)/sum(latencies) if latencies and sum(latencies)>0 else None
        cell['rss_measurement']='maximum post-request process RSS sample; not instantaneous peak'
        cell['gpu_utilization']=None
    return metrics


def settings(value):
    threshold, margin = value.get("threshold", .5), value.get("min_margin", .08)
    if not finite(threshold) or not .5 <= threshold <= 1 or not finite(margin) or not .001 <= margin <= 1:
        raise ValueError("Threshold: 0,50–1; margem: 0,001–1.")
    return threshold, margin


def candidates(catalog):
    """Every eligible entity participates; filters depend on capabilities, never parser names."""
    areas = {a["area_id"]: " ".join(a["names"]) for a in catalog["areas"]}
    result = []
    for row in sorted(catalog["entities"], key=lambda r: r["entity_id"]):
        if row.get("attributes", {}).get("available") is not True:
            continue
        description = " ".join(str(v) for v in [row["name"], *row.get("aliases", []), areas.get(row.get("area_id"), ""), row.get("device_name") or "", row.get("device_class") or ""])
        for action in sorted(set(row["actions"]) & ACTION_TEXT.keys()):
            if action != "query" and row["domain"] in {"sensor", "binary_sensor"}:
                continue
            result.append({"entity_id": row["entity_id"], "intent": action, "description": description,
                           "text": f"{ACTION_TEXT[action]} {description}", "area_id": row.get("area_id"),
                           "actions": row["actions"], "aliases": row.get("aliases", [])})
    return result


def decision_policy(ranking, model, catalog, config, calibration=None):
    threshold, min_margin = settings(config)
    if not ranking:
        return {"ranking": [], "raw_score": None, "confidence": None, "margin": None, "decision_status": "rejected", "reason": "no_eligible_candidate", "proposal": None}
    if any(not finite(r.get("raw_score")) or not -1 <= r["raw_score"] <= 1 for r in ranking):
        raise ValueError("Score inválido")
    ranking = sorted(ranking, key=lambda r: (-r["raw_score"], r["entity_id"], r["intent"]))
    top = ranking[0]
    margin = top["raw_score"] - ranking[1]["raw_score"] if len(ranking) > 1 else 1.0
    confidence = None
    # Calibration is bound to model fingerprint, descriptor catalog, and joint scoring algorithm.
    valid = (calibration and calibration.get("version") == VERSION and calibration.get("model") == model
             and calibration.get("catalog") == digest(catalog) and calibration.get("validated") is True)
    if valid:
        index = min(4, max(0, int(top["raw_score"] * 5)))
        cell = calibration["bins"][index]
        checked = calibration.get("validation_bins", [{}] * 5)[index]
        if cell["n"] >= 10 and checked.get("n", 0) >= 10 and abs(cell["correct"] / cell["n"] - checked["correct"] / checked["n"]) <= .1:
            confidence = cell["correct"] / cell["n"]
    status, reason = "uncalibrated", "joint_calibration_missing"
    if margin < min_margin:
        status, reason = "ambiguous", "insufficient_margin"
    elif confidence is not None:
        status = "accepted" if confidence > threshold else "rejected"
        reason = "joint_calibrated" if status == "accepted" else "below_threshold"
    return {"ranking": ranking[:8], "raw_score": top["raw_score"], "confidence": confidence,
            "margin": margin, "decision_status": status, "reason": reason,
            "proposal": {"intent": top["intent"], "target_entities": [top["entity_id"]], "target_area": None,
                         "confidence": confidence, "requires_clarification": False, "clarification_question": None,
                         "reason": reason} if status == "accepted" else None}


LLM_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {
    "intent": {"type": ["string", "null"], "enum": ["turn_on", "turn_off", "query", "conversation", None]},
    "target_entities": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
    "target_area": {"type": ["string", "null"], "minLength": 1}, "confidence": {"type": "null"},
    "requires_clarification": {"type": "boolean"}, "clarification_question": {"type": ["string", "null"], "minLength": 1, "maxLength": 512},
    "reason": {"type": "string", "maxLength": 512}},
    "required": ["intent", "target_entities", "target_area", "confidence", "requires_clarification", "clarification_question", "reason"]}


def validate_proposal(value, catalog, allowed=None):
    if type(value) is not dict or set(value) != set(LLM_SCHEMA["required"]):
        raise ValueError("Schema de ação inválido")
    if value["intent"] not in ("turn_on", "turn_off", "query", "conversation", None) or type(value["requires_clarification"]) is not bool:
        raise ValueError("Intenção inválida")
    if value["confidence"] is not None and (not finite(value["confidence"]) or not 0 <= value["confidence"] <= 1):
        raise ValueError("Confiança inválida")
    if type(value["reason"]) is not str or len(value["reason"]) > 512:
        raise ValueError("Justificativa inválida")
    question = value["clarification_question"]
    if question is not None and (type(question) is not str or not 1 <= len(question) <= 512):
        raise ValueError("Pergunta inválida")
    if (value["requires_clarification"] and question is None) or (not value["requires_clarification"] and question is not None) or (value["intent"] is None and not value["requires_clarification"]):
        raise ValueError("Esclarecimento e pergunta inconsistentes")
    ids = value["target_entities"]
    if type(ids) is not list or len(ids) > 4 or any(type(x) is not str for x in ids) or len(set(ids)) != len(ids):
        raise ValueError("Alvos inválidos")
    rows = {r["entity_id"]: r for r in catalog["entities"]}
    area = value["target_area"]
    if area is not None and (type(area) is not str or area not in {a["area_id"] for a in catalog["areas"]}):
        raise ValueError("Área inexistente")
    for entity_id in ids:
        row = rows.get(entity_id)
        if row is None or (allowed is not None and entity_id not in allowed) or row.get("attributes", {}).get("available") is not True:
            raise ValueError("Alvo inexistente, indisponível ou fora dos candidatos")
        if (value["intent"] is not None and value["intent"] not in row["actions"]) or (area is not None and row.get("area_id") != area):
            raise ValueError("Ação incompatível")
    if not value["requires_clarification"] and value["intent"] not in (None, "conversation") and not ids:
        raise ValueError("Ação sem alvo")
    if (value["intent"] == "conversation" or (value["intent"] is None and not value["requires_clarification"])) and ids:
        raise ValueError("Conversa não pode conter ação")
    return value


async def pipeline(text, nlu, decision, llm, *, mode="pipeline", timeout=30, general=False):
    if type(text) is not str or not 1 <= len(text) <= 512 or any(ord(c) < 32 for c in text):
        raise ValueError("Comando deve ter 1–512 caracteres, em uma linha")
    if mode not in {"pipeline", "nlu", "decision", "llm"}:
        raise ValueError("Modo inválido")
    started = time.perf_counter()
    stages = {name: {"status": "skipped", "latency_ms": None, "cpu_ms": None, "memory_bytes": None} for name in ("vad", "stt", "nlu", "decision", "llm", "ha", "tts")}
    result = {"text": text, "stages": stages, "proposal": None, "source": None, "requires_review": False}

    async def run(name, fn, payload):
        before = time.perf_counter()
        try:
            async with asyncio.timeout(timeout):
                output = await fn(payload)
            stages[name].update(status="executed", input=payload, output=output)
            if type(output) is dict:
                for metric in ("model", "algorithm", "load_ms", "preparation_ms", "retrieval_ms", "inference_ms", "validation_ms", "queue_ms", "analysis_ms", "cache", "cpu_ms", "memory_bytes"):
                    if metric in output:
                        stages[name][metric] = output[metric]
            return output
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            stages[name].update(status="error", error=type(exc).__name__,
                                error_code=getattr(exc, "code", type(exc).__name__),
                                phase=getattr(exc, "phase", "timeout" if isinstance(exc, TimeoutError) else "validation"),
                                reason=str(exc)[:256])
            return None
        finally:
            stages[name]["latency_ms"] = (time.perf_counter() - before) * 1000

    stages["nlu"]["model"] = "Arandu Rust v4"
    nl = await run("nlu", nlu, text) if mode in ("pipeline", "nlu") else None
    route = None
    blocked_by_route = False
    if nl and nl.get("status") == "plan":
        result.update(proposal=nl, source="nlu")
    elif mode == 'pipeline' and nl:
        from .fallback_policy import RouteCategory, classify_fallback, route_diagnostic
        nl_cmd = nl.get("command") if isinstance(nl.get("command"), dict) else nl
        route = classify_fallback(
            nlu_status=nl.get("status", "no_match"),
            reason=nl.get("reason"),
            command=nl_cmd,
            candidates=nl.get("candidates") or (),
            options=nl.get("options") or (),
            pending=bool(nl.get("pending")),
            safety_flags=nl.get("safety_flags") or (),
            explicitly_denied=bool(nl.get("explicitly_denied")),
            user_scope_known=nl.get("user_scope_known", True),
        )
        result["route_diagnostic"] = route_diagnostic(route)
        if route.category != RouteCategory.RECOVERABLE:
            blocked_by_route = True
            skip_reason = (
                "nlu_safety_or_dialogue_refusal"
                if route.category in (RouteCategory.SAFETY_BLOCK, RouteCategory.PENDING_DIALOGUE)
                else (route.escalation_blocked_by or route.reason or route.category.value.lower())
            )
            stages["decision"]["reason"] = skip_reason
            stages["llm"]["reason"] = skip_reason
            if route.category == RouteCategory.SAFETY_BLOCK:
                stages["decision"]["status"] = "blocked"
                stages["llm"]["status"] = "blocked"
    if not (nl and nl.get("status") == "plan") and not blocked_by_route and mode != "nlu":
        stages["decision"]["fallback_reason"] = (route.reason if route else None) or (nl or {}).get("reason") or (nl or {}).get("status") or "isolated_mode"
        dec_payload = {"text": text, "nlu": nl}
        if route is not None:
            dec_payload["preserved_slots"] = route.preserve_slots
        de = await run("decision", decision, dec_payload) if mode != "llm" and not (general and mode == "pipeline") else None
        if mode != "llm" and de and de.get("decision_status") in {"accepted", "resolved", "confirmation_required"} and de.get("proposal"):
            result.update(proposal=de["proposal"], source="decision")
        elif de and de.get('algorithm') == 'structured-v3' and de.get('decision_status') == 'ambiguous' and (de.get('analysis') or {}).get('device',{}).get('candidate_count',0) > 1:
            stages['llm']['reason'] = 'true_entity_ambiguity'
        elif mode != "decision":
            stages["llm"]["fallback_reason"] = (de or {}).get("reason") or "decision_unavailable"
            llm_payload = {"text": text, "nlu": nl, "decision": de}
            if route is not None:
                llm_payload["preserved_slots"] = route.preserve_slots
            output = await run("llm", llm, llm_payload)
            if output:
                typed=output.get('contract_version')==2 and 'plan' in output
                kind=output.get('kind')
                if kind=='conversation':
                    reply=output.get('response_text')
                    valid_reply=isinstance(reply,str) and 1<=len(reply.strip())<=1000
                    result.update(
                        proposal=None if typed else output.get("proposal"),
                        plan=output.get('plan'),
                        kind='conversation',
                        source="llm",
                        response_text=reply.strip() if valid_reply else None,
                        requires_review=not valid_reply,
                        final_kind='conversation_response' if valid_reply else 'error',
                        clarification_question=None,
                    )
                    if not valid_reply:
                        result.update(status='error',reason='llm_conversation_empty')
                        result['stages']['llm']={**result['stages'].get('llm',{}),'status':'error','reason':'llm_conversation_empty'}
                else:
                    result.update(proposal=None if typed else output.get("proposal", output),plan=output.get('plan'),kind=kind,
                        source="llm", response_text=output.get("response_text"), requires_review=not bool(output.get("response_text")))
        if result.get("route_diagnostic"):
            result["route_diagnostic"]["decision_called"] = stages["decision"]["status"] == "executed"
            result["route_diagnostic"]["llm_called"] = stages["llm"]["status"] == "executed"
    if result.get("kind") != "conversation":
        result["clarification_question"] = ((result.get('plan') or {}).get('question') or
            (result["proposal"] or {}).get("clarification_question") or
            (None if result["proposal"] or (result.get('plan') or {}).get('type')=='automation' else "Não entendi o que você deseja. Pode repetir dizendo o dispositivo e a ação?"))
    result["total_ms"] = (time.perf_counter() - started) * 1000
    return result


def benchmark(rows):
    """Only labelled samples contribute to accuracy; unlabelled samples remain visible."""
    latencies = sorted(r["result"]["total_ms"] for r in rows)
    labelled = [r for r in rows if r.get("expected") is not None]
    correct = false_actions = device_errors = 0
    reliability = []
    for row in labelled:
        expected, result = row["expected"], row["result"]
        actual = row.get("actual")
        matched = actual is not None and all(actual.get(key) == value for key, value in expected.items())
        correct += matched
        false_actions += bool(actual and actual.get("intent") in ("turn_on", "turn_off") and actual.get("target_entities") and not matched)
        device_errors += bool(actual and "target_entities" in expected and actual.get("target_entities") != expected["target_entities"])
        de = result["stages"]["decision"].get("output") or {}
        if finite(de.get("confidence")):
            top = (de.get("ranking") or [{}])[0]
            prediction = {"intent": top.get("intent"), "target_entities": [top.get("entity_id")], "target_area": top.get("area_id"), "requires_clarification": False}
            reliability.append({"confidence": de["confidence"], "correct": all(prediction.get(key) == value for key, value in expected.items())})
    n = len(rows)
    memory = [s["memory_bytes"] for r in rows for s in r["result"]["stages"].values() if finite(s.get("memory_bytes"))]
    return {"samples": n, "labelled": len(labelled), "accuracy": correct / len(labelled) if labelled else None,
            "device_errors": device_errors if labelled else None, "false_activations": false_actions if labelled else None,
            "rejection_rate": sum(not r["result"].get("operations_validated", False) for r in rows) / n if n else None,
            "llm_rate": sum(r["result"]["stages"]["llm"]["status"] != "skipped" for r in rows) / n if n else None,
            "latency_ms": {f"p{p}": latencies[max(0, math.ceil(n * p / 100) - 1)] if n else None for p in (50, 95, 99)},
            "memory_bytes": max(memory) if memory else None, "reliability": reliability,
            "routes": dict(Counter(r["result"].get("source") or "clarification" for r in rows))}
