"""Click-to-run synthetic benchmark. No execution boundary or HA service calls."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import time

from .ai_lab_core import MODELS, digest, finite, validate_proposal
from .contextual_protocol import parse


def suite():
    """Load human-readable frozen labels; never derive gold answers from model outputs."""
    data = json.loads(Path(__file__).with_name("ai_lab_demo.json").read_text(encoding="utf-8"))
    entities = []
    for item in data["devices"]:
        domain = item["entity_id"].split(".", 1)[0]
        available = item.get("available", True)
        effects = domain in {"light", "fan", "switch"} and available
        entities.append({"registry_id": "demo_" + item["id"], "entity_id": item["entity_id"], "domain": domain,
            "area_id": item["area"], "device_id": "demo_device_" + item["id"], "device_name": item["name"],
            "name": item["name"], "aliases": item["aliases"], "device_class": item.get("device_class"),
            "actions": ["query", "turn_on", "turn_off"] if effects else ["query"],
            "attributes": {"available": available, "bulk_eligible": effects}, "preferred": item.get("preferred", False)})
    catalog = {"version": 4, "entities": sorted(entities, key=lambda r: r["registry_id"]), "areas": data["areas"], "groups": []}
    catalog["generation"] = digest(catalog)
    runners = [{"id": "nlu", "kind": "nlu", "model": "rust", "name": "NLU Rust"}]
    runners += [{"id": key, "kind": item["kind"], "model": key, "name": item["name"]} for key, item in MODELS.items()]
    return {**data, "catalog": catalog, "dataset_hash": digest(data), "runners": runners,
            "dry_run": True, "automatic_execution": False, "decision_config": {"threshold": .5, "min_margin": .08}}


def canonical(intent=None, targets=(), clarification=True):
    return {"intent": None if clarification else intent, "target_entities": [] if clarification else sorted(set(targets)), "requires_clarification": clarification}


async def run_case(payload, nlu_client, infer):
    """One invocation per request allows live browser progress and stopping between cases."""
    data = suite()
    if payload.get("dataset_hash") != data["dataset_hash"]:
        raise ValueError("Dataset alterado. Recarregue a suíte antes de executar.")
    case = next((c for c in data["cases"] if c["id"] == payload.get("case_id")), None)
    runner = next((r for r in data["runners"] if r["id"] == payload.get("runner_id")), None)
    if not case or not runner:
        raise ValueError("Caso ou modelo de demonstração desconhecido")
    catalog, text = data["catalog"], case["text"]
    rows = {r["entity_id"]: r for r in catalog["entities"]}
    by_registry = {r["registry_id"]: r["entity_id"] for r in catalog["entities"]}
    started = time.perf_counter()
    stages = []

    async def stage(name, model, fn):
        before = time.perf_counter()
        entry = {"stage": name, "model": model, "status": "running", "load_ms": None, "inference_ms": None,
                 "cpu_ms": None, "memory_bytes": None, "input": {"text": text, "catalog_generation": catalog["generation"]}}
        stages.append(entry)
        try:
            async with asyncio.timeout(95):
                output = await fn()
            entry.update(status="executed", output=output)
            if isinstance(output, dict):
                entry.update({k: output[k] for k in ("load_ms", "inference_ms", "cpu_ms", "memory_bytes") if k in output})
            return output
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            entry.update(status="error", error=type(exc).__name__, reason=str(exc)[:512])
            return None
        finally:
            entry["latency_ms"] = (time.perf_counter() - before) * 1000

    async def nlu():
        acknowledgement = await nlu_client.async_catalog_v4(catalog)
        if parse(acknowledgement).status != "catalog_ready":
            raise ValueError("Rust recusou o catálogo fictício")
        output = await nlu_client.async_interpret_v4({"version": 4, "generation": catalog["generation"], "text": text,
            "origin_area": None, "origin_area_source": "unknown", "last_area": None, "last_targets": [], "pending": None})
        if parse(output).status in {"invalid_request", "stale", "catalog_ready"}:
            raise ValueError("NLU não conseguiu avaliar o caso: " + output["status"])
        return output

    async def decision(model):
        result = await infer({"action": "decision", "model": model, "text": text, "catalog": catalog,
                              "config": data["decision_config"]})
        if result.get("decision_status") not in {"accepted", "ambiguous", "rejected", "uncalibrated"} or type(result.get("ranking")) is not list:
            raise ValueError("Resposta Decision inválida")
        if result["decision_status"] == "accepted" and (not finite(result.get("confidence")) or result["confidence"] <= .5 or not finite(result.get("margin")) or result["margin"] < .08):
            raise ValueError("Aceitação sem confiança calibrada/margem válidas")
        return result

    def check_action(value):
        validate_proposal(value, catalog)
        if value["requires_clarification"] or value["intent"] in (None, "conversation"):
            return canonical()
        for entity in value["target_entities"]:
            row = rows[entity]
            if row["attributes"]["available"] is not True or value["intent"] not in row["actions"]:
                raise ValueError("Proposta incompatível com o catálogo fictício")
        return canonical(value["intent"], value["target_entities"], False)

    actual, raw_prediction, output = None, None, None
    try:
        if runner["kind"] == "nlu":
            output = await stage("nlu", "rust", nlu)
            if output:
                outcome = parse(output)
                if outcome.status == "plan":
                    actions = {op.action for op in outcome.operations}
                    targets = [by_registry[t] for op in outcome.operations for t in op.targets]
                    if len(actions) != 1 or any(rows[by_registry[t]]["attributes"]["available"] is not True or op.action not in rows[by_registry[t]]["actions"] for op in outcome.operations for t in op.targets):
                        raise ValueError("Plano fictício não validável")
                    actual = canonical(next(iter(actions)), targets, False)
                else:
                    actual = canonical()
                raw_prediction = actual
        elif runner["kind"] == "decision":
            output = await stage("decision", runner["model"], lambda: decision(runner["model"]))
            if output:
                top = (output.get("ranking") or [None])[0]
                if top:
                    raw_prediction = canonical(top["intent"], [top["entity_id"]], False)
                actual = check_action(output["proposal"]) if output.get("decision_status") == "accepted" and output.get("proposal") else canonical()
        else:
            # Tucano package can retrieve independently; no mandatory Decision dependency.
            retrieval = None if runner['model'] == 'tucano' else await stage("retrieval", "brazembed", lambda: decision("brazembed"))
            async def llm():
                result = await infer({"action": "llm", "llm_model": runner["model"], "text": text, "nlu": None,
                                      "decision": retrieval, "catalog": catalog})
                used = retrieval or result.get('retrieval') or {}
                validate_proposal(result["proposal"], catalog, {r["entity_id"] for r in used.get("ranking", [])})
                return result
            output = await stage("llm", runner["model"], llm)
            if output:
                actual = check_action(output["proposal"])
                raw_prediction = actual
    except (KeyError, ValueError, TypeError) as exc:
        stages.append({"stage": "validation", "model": runner["model"], "status": "error", "reason": str(exc)[:512], "latency_ms": 0})
        actual = None
    errors = [s for s in stages if s["status"] == "error"]
    target_stage = next((s for s in reversed(stages) if s["stage"] == runner["kind"]), None)
    expected = case["expected"]
    expected = canonical(expected["intent"], expected["target_entities"], expected["requires_clarification"])
    correct = not errors and actual == expected
    positive = expected["requires_clarification"] is False
    return {"demo": True, "synthetic": True, "dry_run": True, "execution_allowed": False,
            "dataset_id": data["id"], "dataset_hash": data["dataset_hash"], "catalog_generation": catalog["generation"],
            "decision_config": data["decision_config"],
            "case_id": case["id"], "text": text, "category": case["category"], "runner": runner,
            "expected": expected, "actual": actual, "raw_prediction": raw_prediction,
            "correct": bool(correct), "top1_correct": (not errors and raw_prediction == expected) if positive else None,
            "device_error": bool(actual and not actual["requires_clarification"] and actual["target_entities"] != expected["target_entities"]),
            "false_action": bool(actual and actual["intent"] in ("turn_on", "turn_off") and not correct),
            "error": bool(errors), "stages": stages, "output": output,
            "model_invocations": int(target_stage is not None), "support_invocations": sum(s["stage"] == "retrieval" for s in stages),
            "model_ms": target_stage.get("latency_ms") if target_stage else None,
            "load_ms": target_stage.get("load_ms") if target_stage else None,
            "inference_ms": target_stage.get("inference_ms") if target_stage else None,
            "total_ms": (time.perf_counter() - started) * 1000}
