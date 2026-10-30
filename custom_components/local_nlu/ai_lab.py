"""Admin-only laboratory. The existing HA runtime remains the only executor."""
from __future__ import annotations

import asyncio
import base64
from dataclasses import asdict
import json
from pathlib import Path
import secrets
import time
from types import SimpleNamespace

from .ai_lab_core import MODELS, benchmark, digest, finite, pipeline, settings, validate_proposal
from .client import LocalNluClient


class StageError(ValueError):
    """Safe structured diagnostics; upstream bodies may contain secrets."""
    def __init__(self, code, phase, message):
        self.code, self.phase = code, phase
        super().__init__(message)


async def local_request(session, endpoint, path, *, payload=None, body=None, timeout=90, headers=None, binary=False):
    """Reuse production local-origin validation and DNS pinning; never follow redirects."""
    from aiohttp import ClientTimeout
    client = LocalNluClient(session, endpoint)
    origin = await client._async_pinned_origin()
    data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode() if payload is not None else body
    if data is not None and len(data) > 2_097_152:
        raise ValueError("Entrada excedeu 2 MiB")
    request_headers = {"Host": client._host_header, "X-Arandu-Lab": "1", **(headers or {})}
    if payload is not None:
        request_headers["Content-Type"] = "application/json"
    limit = 4_194_304 if binary else 2_097_152
    async with session.request("POST" if data is not None else "GET", origin + path, data=data, headers=request_headers,
                               timeout=ClientTimeout(total=timeout), allow_redirects=False) as response:
        raw = bytearray()
        async for chunk in response.content.iter_chunked(8192):
            raw.extend(chunk)
            if len(raw) > limit:
                raise ValueError("Resposta excedeu limite")
        if response.status != 200:
            code, phase = f"http_{response.status}", "connection" if response.status in (502, 503, 504) else "inference"
            try:
                diagnostic = json.loads(raw)
                upstream = diagnostic.get("code", "")
                if type(upstream) is str and upstream.startswith("upstream_http_") and upstream.removeprefix("upstream_http_").isdigit():
                    code = upstream
                    phase = diagnostic.get("phase") if diagnostic.get("phase") in {"loading", "retrieval", "inference", "validation"} else "unknown"
            except (ValueError, AttributeError):
                pass
            raise StageError(code, phase,
                             f"Serviço local retornou HTTP {response.status}. Consulte os logs do módulo.")
        return (bytes(raw), dict(response.headers)) if binary else json.loads(raw)


class Lab:
    def __init__(self, hass):
        self.hass = hass
        self.lock = asyncio.Lock()
        self.proposals = {}

    async def handle(self, user, payload):
        if user is None or user.is_active is not True or user.is_admin is not True:
            raise ValueError("AI Lab exige administrador ativo")
        from homeassistant.helpers.aiohttp_client import async_get_clientsession
        session = async_get_clientsession(self.hass)
        command = payload.get("command", "run")
        from .const import DOMAIN
        entries = self.hass.config_entries.async_entries(DOMAIN)
        selected = next((e for e in entries if e.entry_id == payload.get('entry_id')), None)
        if selected is None and len(entries) == 1:
            selected = entries[0]
        stack_endpoint = selected.options.get('stack_endpoint', selected.data.get('stack_endpoint', '')) if selected else ''
        worker = payload.get('worker') or stack_endpoint or 'http://127.0.0.1:11556'
        if command in {"demo_suite", "demo_case"}:
            from .ai_lab_demo import suite, run_case
            if command == "demo_suite":
                return suite()
            if self.lock.locked():
                raise ValueError("Laboratório ocupado; aguarde")
            from .const import DOMAIN
            entries = self.hass.config_entries.async_entries(DOMAIN)
            entry = next((e for e in entries if e.entry_id == payload.get("entry_id")), None)
            if entry is None and len(entries) == 1:
                entry = entries[0]
            if entry is None:
                raise ValueError("Selecione a entrada NLU")
            async def demo_infer(body):
                return await local_request(session, worker, "/infer", payload=body)
            async with self.lock:
                return await run_case(payload, entry.runtime_data._client, demo_infer)
        if command == "status":
            try:
                return await local_request(session, worker, "/status", timeout=5)
            except Exception as exc:
                return {"models": [{"id": key, **model, "available": False, "installed": None,
                                     "reason": model.get("unavailable") or "Worker inacessível; instalação não verificada"} for key, model in MODELS.items()], "error": str(exc)[:256]}
        if command == "transcribe":
            audio = base64.b64decode(payload["audio"], validate=True)
            if not 1 <= len(audio) <= 2_000_000:
                raise ValueError("Áudio excedeu 2 MB")
            started = time.perf_counter()
            result = await local_request(session, payload.get("stt_endpoint", "http://127.0.0.1:8765"), "/api/compare-upload", body=audio,
                                         headers={"Content-Type": "application/octet-stream"})
            selected = payload.get("stt_model", "original")
            if selected not in ("original", "adapted") or not result.get(selected):
                raise ValueError("Modelo STT não disponível")
            return {"text": result[selected]["text"], "model": selected, "latency_ms": (time.perf_counter() - started) * 1000,
                    "output": result[selected], "vad_ms": None}
        if command == "tts":
            text = payload.get("text")
            if type(text) is not str or not 1 <= len(text) <= 512:
                raise ValueError("Texto TTS inválido")
            engine = payload.get("tts_model", "piper")
            if engine not in ("piper", "pocket"):
                raise ValueError("Motor TTS inválido")
            before = time.perf_counter()
            audio, metadata = await local_request(session, stack_endpoint or payload.get("tts_endpoint", "http://127.0.0.1:8099"), "/synthesize" if stack_endpoint else "/api/synthesize",
                payload={"text": text, "engine": engine}, headers={"X-Arandu-UI": "1", "X-Arandu-Token": payload.get("tts_token", "")}, binary=True)
            if not audio.startswith(b"RIFF"):
                raise ValueError("TTS não retornou WAV")
            return {"audio": base64.b64encode(audio).decode(), "latency_ms": (time.perf_counter() - before) * 1000,
                    "first_chunk_ms": metadata.get("X-Arandu-Ttfb-Ms"), "model": engine}
        if self.lock.locked():
            raise ValueError("Laboratório ocupado; aguarde")
        async with self.lock:
            return await self._handle(user, payload, session, worker)

    async def _handle(self, user, payload, session, worker):
        from homeassistant.core import Context
        from .contextual_catalog import build, origin_area
        from .contextual_runtime import ContextualRuntime, Session
        from .contextual_protocol import Operation, parse
        from .identity import ResolvedIdentity
        from .const import DOMAIN
        entries = self.hass.config_entries.async_entries(DOMAIN)
        entry = next((e for e in entries if e.entry_id == payload.get("entry_id")), None)
        if entry is None:
            if len(entries) != 1:
                raise ValueError("Selecione entry_id da integração")
            entry = entries[0]
        runtime = ContextualRuntime(self.hass, entry.runtime_data._client, lambda: dict(entry.options))
        try:
            options = runtime.current_options()
            runtime.request_revision = runtime.options_revision
            snapshot = build(self.hass, user, options)
            catalog = snapshot.payload
            command = payload.get("command", "run")
            now = time.monotonic()
            self.proposals = {k: v for k, v in self.proposals.items() if v["expires"] > now}
            if command == "catalog":
                return {**catalog, "entities": [{**row, "state": (state.state[:255] if (state := self.hass.states.get(row["entity_id"])) else None)} for row in catalog["entities"]]}
            if command == "readiness":
                policy = {"nlu": True, "decision": options.get("decision_enabled", False), "llm": options.get("llm_enabled", False)}
                try:
                    readiness = await local_request(session, worker, "/infer", payload={"action": "readiness", "catalog": catalog, "model": "e5-small"}, timeout=5)
                except Exception:
                    readiness = {"calibration_valid": None, "can_accept": False, "reason": "Serviço Decision indisponível; calibração não verificada."}
                return {"production_policy": policy, "decision": readiness}
            if command == "execute":
                saved = self.proposals.pop(payload.get("token"), None)
                if not saved or saved["user"] != user.id or saved["entry"] != entry.entry_id or saved["catalog"] != digest(catalog) or saved["options"] != digest(options):
                    raise ValueError("Proposta expirada, alterada ou já utilizada; processe novamente")
                if payload.get("confirm") is not True:
                    raise ValueError("Confirmação explícita obrigatória")
                input_ = SimpleNamespace(text=saved["text"], context=Context(user_id=user.id), conversation_id=secrets.token_hex(16), device_id=None, satellite_id=None, language="pt-BR")
                # No confirmed=True: production independently blocks sensitive operations.
                result = await runtime.execute(saved["operations"], snapshot, input_, Session(now + 30), options, ResolvedIdentity(user.id, user, "context"))
                return asdict(result)
            if command in ("unload", "load", "install", "calibrate"):
                body = {"action": command, "catalog": catalog, "model": payload.get("model", "e5-small")}
                if command == "calibrate":
                    body.update(train=payload["train"], validation=payload["validation"])
                return await local_request(session, worker, "/infer", payload=body, timeout=180)
            if command not in ("run", "compare"):
                raise ValueError("Comando desconhecido")
            settings(payload)
            await runtime._send(snapshot)

            async def nlu(text):
                raw = await runtime.client.async_interpret_v4({"version": 4, "generation": catalog["generation"], "text": text,
                    "origin_area": origin_area(self.hass, SimpleNamespace(device_id=None, satellite_id=None), options), "origin_area_source": "default" if options.get("default_area") else "unknown", "last_area": None, "last_targets": [], "pending": None})
                parse(raw)
                return raw

            async def run(text, model, mode):
                async def decision(data):
                    threshold, margin = settings(payload)
                    response = await local_request(session, worker, "/infer", payload={**data, "action": "decision", "catalog": catalog,
                        "model": model, "config": {"threshold": payload.get("threshold", .5), "min_margin": payload.get("min_margin", .08)}})
                    if response.get("decision_status") == "accepted":
                        confidence, gap = response.get("confidence"), response.get("margin")
                        if not finite(confidence) or confidence <= threshold or not finite(gap) or gap < margin:
                            raise ValueError("Worker retornou aceitação sem confiança/margem válidas")
                        proposed = validate_proposal(response.get("proposal"), catalog)
                        top = response["ranking"][0]
                        if proposed["target_entities"] != [top["entity_id"]] or proposed["intent"] != top["intent"] or proposed["confidence"] != confidence:
                            raise ValueError("Proposta não corresponde ao ranking calibrado")
                    return response
                async def llm(data):
                    # LLM service performs its own retrieval when Decision was not called.
                    raw = await local_request(session, worker, "/infer", payload={**data, "action": "llm", "catalog": catalog, "llm_model": payload.get("llm_model", "tucano")})
                    retrieval = raw.get("retrieval") or data.get("decision") or {}
                    proposal = validate_proposal(raw["proposal"], catalog, {r["entity_id"] for r in retrieval.get("ranking", [])})
                    proposal["confidence"] = None
                    return {**raw, "proposal": proposal}
                result = await pipeline(text, nlu, decision, llm, mode=mode, timeout=95)
                result.update(dry_run=True, catalog_generation=catalog["generation"], model=model,
                              catalog=catalog, test_mode="independent",
                              production_policy={"nlu": True, "decision": options.get("decision_enabled", False), "llm": options.get("llm_enabled", False)})
                proposal = result["proposal"]
                operations = ()
                if proposal and result["source"] == "nlu":
                    operations = parse(proposal).operations
                elif proposal:
                    validate_proposal(proposal, catalog)
                    if not proposal["requires_clarification"] and proposal["intent"] not in ("conversation", None):
                        by_entity = {row["entity_id"]: key for key, row in snapshot.by_id.items()}
                        operations = (Operation("lab." + proposal["intent"], proposal["intent"], tuple(sorted(by_entity[e] for e in proposal["target_entities"])), {}, evidence=("ai_lab_review",)),)
                if operations:
                    validation_started = time.perf_counter()
                    try:
                        prepared = tuple(runtime.prepare(op, snapshot, user, options) for op in operations)
                        runtime.validate_plan(prepared)
                        runtime.validate_bulk_snapshot(operations, snapshot, user, options)
                        result["action_json"] = [op.as_dict() for op in operations]
                        result["target_entities"] = sorted({snapshot.by_id[t]["entity_id"] for op in operations for t in op.targets})
                        result["sensitive"] = any(p.needs_confirmation for p in prepared)
                        result["stages"]["validation"] = {"status": "executed", "result": "confirmation_required" if result["sensitive"] else "accepted"}
                    except Exception as exc:
                        result.update(validation_error=str(exc), action_json=None, clarification_question="Não foi possível validar essa ação no Home Assistant.")
                        result["stages"]["validation"] = {"status": "error", "error": type(exc).__name__, "phase": "validation", "reason": str(exc)[:256]}
                        operations = ()
                    result["stages"]["validation"]["latency_ms"] = (time.perf_counter() - validation_started) * 1000
                else:
                    result["stages"]["validation"] = {"status": "skipped", "reason": "Nenhuma operação proposta"}
                result["stages"]["response"] = {"status": "executed", "reason": result["clarification_question"] or "Diagnóstico concluído"}
                result["stages"] = {name: result["stages"][name] for name in ("vad", "stt", "nlu", "decision", "llm", "validation", "ha", "response", "tts")}
                result["operations_validated"] = bool(operations)
                return result, operations

            if command == "compare":
                models = payload.get("models", [payload.get("model", "e5-small")])
                examples = payload.get("examples", [])
                if type(models) is not list or not 1 <= len(models) <= 4 or any(m not in ("brazembed", "e5-small", "saracura", "jatoba") for m in models) or type(examples) is not list or not 1 <= len(examples) <= 20:
                    raise ValueError("Compare 1–4 motores e 1–20 exemplos por lote")
                output = {}
                for example in examples:
                    if type(example) is not dict or type(example.get("text")) is not str:
                        raise ValueError("Exemplo deve conter text")
                    label = example.get("expected")
                    if label is not None and (type(label) is not dict or not label or not set(label) <= {"intent", "target_entities", "target_area", "requires_clarification"}):
                        raise ValueError("Rótulo expected inválido")
                for model in models:
                    rows = []
                    for example in examples:
                        result, operations = await run(example["text"], model, payload.get("mode", "pipeline"))
                        expected = example.get("expected")
                        actual = None
                        if operations:
                            areas = {snapshot.by_id[t].get("area_id") for op in operations for t in op.targets}
                            actual = {"intent": operations[0].action, "target_entities": result["target_entities"], "target_area": next(iter(areas)) if len(areas) == 1 else None, "requires_clarification": False}
                        elif expected is not None:
                            actual = {"intent": None, "target_entities": [], "target_area": None, "requires_clarification": True}
                        rows.append({"expected": expected, "actual": actual, "result": result})
                    output[model] = {"rows": rows, "metrics": benchmark(rows)}
                return {"catalog_generation": catalog["generation"], "sequential": True, "dry_run": True, "models": output}
            result, operations = await run(payload.get("text"), payload.get("model", "e5-small"), payload.get("mode", "pipeline"))
            if operations and not result.get("sensitive"):
                token = secrets.token_urlsafe(24)
                if len(self.proposals) >= 32:
                    self.proposals.pop(next(iter(self.proposals)))
                self.proposals[token] = {"user": user.id, "entry": entry.entry_id, "catalog": digest(catalog), "options": digest(options),
                                         "operations": operations, "text": result["text"], "expires": time.monotonic() + 30}
                result["execution_token"] = token
            return result
        finally:
            runtime.close()


async def async_register(hass):
    """Register one sidebar panel and authenticated websocket route for all entries."""
    if "local_nlu_ai_lab" in hass.data:
        return
    from homeassistant.components import panel_custom, websocket_api
    from homeassistant.components.http import StaticPathConfig
    lab = Lab(hass)

    @websocket_api.websocket_command({"type": "local_nlu/ai_lab", "payload": dict})
    @websocket_api.async_response
    async def handle(connection_hass, connection, msg):
        try:
            result = await lab.handle(connection.user, msg["payload"])
            connection.send_result(msg["id"], result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            connection.send_error(msg["id"], "ai_lab_error", str(exc)[:256])
    websocket_api.async_register_command(hass, handle)
    await hass.http.async_register_static_paths([StaticPathConfig("/local_nlu_ai_lab/panel.js", str(Path(__file__).with_name("ai_lab_panel.js")), False)])
    await panel_custom.async_register_panel(hass, webcomponent_name="arandu-ai-lab", frontend_url_path="arandu-ai-lab",
        module_url="/local_nlu_ai_lab/panel.js", sidebar_title="Arandu AI Lab", sidebar_icon="mdi:flask-outline", require_admin=True)
    hass.data["local_nlu_ai_lab"] = lab
