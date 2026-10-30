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
    "brazembed": {"name": "BrazEmbed PT-BR 110M", "repo": "tardellirs/brazembed-pt-br", "license": "MIT", "runtime": "sentence-transformers / CPU", "kind": "decision"},
    "e5-small": {"name": "Multilingual E5 Small", "repo": "intfloat/multilingual-e5-small", "license": "MIT", "runtime": "sentence-transformers / CPU; query/passsage prefixes", "kind": "decision"},
    "saracura": {"name": "Saracura PT-BR 322M", "repo": "felhen-ai/saracura-ptbr-v0", "license": "Apache-2.0", "kind": "decision", "runtime": "Laya / CPU", "github": "https://github.com/felhen-ai/saracura"},
    "jatoba": {"name": "JATOBÁ Decision 169M · não comercial", "repo": "leoabreu288/jatoba-decision", "license": "CC-BY-NC-SA-4.0 (pesos); Apache-2.0 (código)", "kind": "decision", "runtime": "jatoba / CPU", "github": "https://github.com/LeonardoVilela/jatoba-decision", "noncommercial": True},
    "qwen": {"name": "Qwen3.5 0.8B Q4_0", "repo": "ggml-org/Qwen3.5-0.8B-GGUF", "license": "Apache-2.0", "kind": "llm", "file": "Qwen3.5-0.8B-Q4_0.gguf", "runtime": "llama.cpp / CPU"},
    "tucano": {"name": "Tucano 2 Qwen 0.5B Instruct Q4_K_M", "repo": "2DTi/tucano2-0.5b-instruct-gguf", "source_repo": "Polygl0t/Tucano2-qwen-0.5B-Instruct", "license": "Apache-2.0", "kind": "llm", "file": "tucano2-0.5b-instruct-Q4_K_M.gguf", "runtime": "llama.cpp / CPU", "github": "https://github.com/Polygl0t/llm-foundry"},
}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


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
        if value["intent"] not in row["actions"] or (area is not None and row.get("area_id") != area):
            raise ValueError("Ação incompatível")
    if not value["requires_clarification"] and value["intent"] not in (None, "conversation") and not ids:
        raise ValueError("Ação sem alvo")
    if value["intent"] in (None, "conversation") and ids:
        raise ValueError("Conversa não pode conter ação")
    return value


async def pipeline(text, nlu, decision, llm, *, mode="pipeline", timeout=30):
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
                for metric in ("model", "load_ms", "retrieval_ms", "inference_ms", "cpu_ms", "memory_bytes"):
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
    if nl and nl.get("status") == "plan":
        result.update(proposal=nl, source="nlu")
    elif mode != "nlu":
        stages["decision"]["fallback_reason"] = (nl or {}).get("reason") or (nl or {}).get("status") or "isolated_mode"
        de = await run("decision", decision, {"text": text, "nlu": nl}) if mode != "llm" else None
        if mode != "llm" and de and de.get("decision_status") == "accepted" and de.get("proposal"):
            result.update(proposal=de["proposal"], source="decision")
        elif mode != "decision":
            stages["llm"]["fallback_reason"] = (de or {}).get("reason") or "decision_unavailable"
            output = await run("llm", llm, {"text": text, "nlu": nl, "decision": de})
            if output:
                result.update(proposal=output.get("proposal", output), source="llm", requires_review=True)
    result["clarification_question"] = (result["proposal"] or {}).get("clarification_question") or (None if result["proposal"] else "Qual dispositivo e ação você deseja?")
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
