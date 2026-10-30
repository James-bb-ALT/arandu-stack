"""Loopback-only service. No HA credentials, execution, or implicit downloads."""
from __future__ import annotations
import argparse
import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
import time
from .contracts import MODELS, candidates, digest
from .adapter import Llama, LlmOutputError
from .semantic_context import resolve_device, decomposition

KEY = "tucano"
PORT = 11558
REVISION = "cbb5318b66a49e20967623e946365bcc8594c29a"

def validate_catalog(catalog):
    if not isinstance(catalog, dict) or not isinstance(catalog.get("entities"), list) or not isinstance(catalog.get("areas"), list):
        raise ValueError("Catálogo completo entities/areas obrigatório")
    if len(catalog["entities"]) > 8192 or len(catalog["areas"]) > 2048:
        raise ValueError("Catálogo excede limite; não será truncado")
    ids = []
    for row in catalog["entities"]:
        if not isinstance(row, dict) or any(k not in row for k in ("entity_id", "name", "domain", "actions")):
            raise ValueError("Entidade incompleta")
        if not isinstance(row["entity_id"], str) or not isinstance(row["actions"], list):
            raise ValueError("Entidade inválida")
        ids.append(row["entity_id"])
    if len(set(ids)) != len(ids):
        raise ValueError("IDs duplicados")
    for area in catalog["areas"]:
        if not isinstance(area, dict) or not isinstance(area.get("area_id"), str) or not isinstance(area.get("names"), list) or any(not isinstance(n, str) for n in area["names"]):
            raise ValueError("Área inválida")
    for row in catalog["entities"]:
        if not isinstance(row["name"], str) or not isinstance(row["domain"], str) or any(not isinstance(a, str) for a in row["actions"]):
            raise ValueError("Nome, domínio ou ações inválidos")
        if not isinstance(row.get("aliases", []), list) or any(not isinstance(a, str) for a in row.get("aliases", [])):
            raise ValueError("Aliases inválidos")
        if not isinstance(row.get("attributes", {}), dict):
            raise ValueError("Atributos inválidos")
    candidates(catalog)
    return catalog

def retrieve(text, catalog, context=None):
    """Standalone lexical retrieval visits every eligible pair; never first-N catalog."""
    import re
    import unicodedata
    def words(value):
        value = unicodedata.normalize("NFKD", value.casefold())
        return set(re.findall(r"[a-z0-9]+", "".join(c for c in value if not unicodedata.combining(c)))) - {"a", "o", "da", "do", "de", "na", "no", "e"}
    query = words(text)
    resolved = resolve_device(text, catalog, context)
    ranking = [{**r, "raw_score": len(query & words(r["text"])) / max(1, len(query))} for r in candidates(catalog)]
    ranking = sorted(ranking, key=lambda r: (not bool(resolved and r["entity_id"] == resolved["entity_id"]), -r["raw_score"], r["entity_id"], r["intent"]))
    return {"ranking": [r for r in ranking if r["raw_score"] > 0][:8], "retrieval": "lexical-full-catalog", "eligible_pairs": len(ranking)}

def create_app(root, executable=None, threads=2, runtime_settings=None):
    from aiohttp import web
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false")
    adapter = Llama(root, executable, threads, settings=runtime_settings)
    lock, tasks = asyncio.Lock(), set()

    @web.middleware
    async def boundary(request, handler):
        import ipaddress
        peer = ipaddress.ip_address(request.remote or "0.0.0.0")
        internal = peer in ipaddress.ip_network("172.30.32.0/23")
        if request.headers.get("Origin") or (not internal and not peer.is_loopback) or (not internal and request.headers.get("Host") not in {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}) or request.headers.get("X-Arandu-Lab") != "1":
            raise web.HTTPForbidden()
        try:
            return await handler(request)
        except LlmOutputError as exc:
            import logging
            logging.getLogger('arandu.llm').warning('Saída recusada: code=%s phase=validation', exc.code)
            return web.json_response({"error": str(exc), "code": exc.code, "phase": "validation"}, status=422)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            import logging
            logging.getLogger('arandu.llm').exception('Falha na solicitação de inferência/configuração')
            return web.json_response({"error": "Falha no módulo; consulte os logs.", "error_type": type(exc).__name__,
                "phase": getattr(adapter, "phase", "validation")}, status=422)

    app = web.Application(client_max_size=2_097_152, middlewares=[boundary])
    async def status(request):
        installed = (root / KEY / "arandu-model.json").is_file() and (root / KEY / MODELS[KEY]["file"]).is_file()
        available = installed and bool(executable and Path(executable).is_file())
        return web.json_response({"package": "arandu-llm", "version": "0.2.0", "protocol": "arandu-inference-v1",
            "models": [{"id": KEY, **MODELS[KEY], "installed": installed, "available": bool(available),
                "reason": None if available else "Pesos ou runtime ausentes"}], "busy": lock.locked(), "threads": threads})

    async def infer(request):
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("Objeto JSON obrigatório")
        action = body.get("action", "llm")
        if body.get("llm_model", KEY) != KEY:
            raise ValueError("Este pacote usa somente Tucano; selecione o serviço correspondente")
        if body.get("model", KEY) != KEY and action != "llm":
            raise ValueError("Modelo não pertence a este pacote")
        if lock.locked():
            return web.json_response({"error": "Serviço ocupado"}, status=429)
        async def work():
            async with lock:
                if action == "unload":
                    await adapter.unload()
                    return {"unloaded": True}
                if action not in {"llm"}:
                    raise ValueError("Ação indisponível neste pacote")
                if action == "load":
                    return {"model_fingerprint": await asyncio.to_thread(adapter.load, KEY), "load_ms": adapter.load_ms}
                catalog = validate_catalog(body.get("catalog"))
                if action == "calibrate":
                    return await asyncio.to_thread(adapter.calibrate, catalog, KEY, body["train"], body["validation"])
                text = body.get("text")
                if not isinstance(text, str) or not 1 <= len(text) <= 512 or any(ord(c) < 32 for c in text):
                    raise ValueError("Texto deve ter 1–512 caracteres em uma linha")
                before = time.perf_counter()
                external = body.get("decision") or {}
                resolved = resolve_device(text, catalog, body.get("context"))
                external_ids = {r["entity_id"] for r in external.get("ranking", [])}
                retrieval = external if external_ids and (resolved is None or resolved["entity_id"] in external_ids) else retrieve(text, catalog, body.get("context"))
                retrieval = {**retrieval, "analysis": external.get("analysis") or decomposition(text, catalog, body.get("context")),
                             "decision_status": external.get("decision_status"), "reason": external.get("reason")}
                retrieval_ms = (time.perf_counter() - before) * 1000
                result = await adapter.infer({**body, "llm_model": KEY, "decision": retrieval}, catalog)
                result["retrieval"] = retrieval
                result["retrieval_ms"] = retrieval_ms
                result.update(catalog_fingerprint=digest(catalog), catalog_entities=len(catalog["entities"]), service="arandu-llm")
                return result
        task = asyncio.create_task(work())
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        try:
            async with asyncio.timeout(180):
                return web.json_response(await asyncio.shield(task))
        except TimeoutError:
            return web.json_response({"error": "Timeout; aguarde liberação do serviço"}, status=504)
    async def cleanup(app):
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await adapter.unload()
    app.on_cleanup.append(cleanup)
    app.router.add_get("/status", status)
    app.router.add_post("/infer", infer)
    return app

def install(root):
    from huggingface_hub import snapshot_download, hf_hub_download
    directory = root / KEY
    info = MODELS[KEY]
    if info["kind"] == "llm":
        hf_hub_download(info["repo"], info["file"], revision=REVISION, local_dir=directory, token=False)
    else:
        snapshot_download(info["repo"], revision=REVISION, local_dir=directory, token=False,
            allow_patterns=["*.json", "*.safetensors", "*.txt", "*.model", "1_Pooling/*", "README.md", "LICENSE*"])
    (directory / "arandu-model.json").write_text(json.dumps({"repo": info["repo"], "revision": REVISION, "license": info["license"]}), encoding="utf-8")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["serve", "install"])
    parser.add_argument("--models", type=Path, default=Path.home() / ".cache" / "arandu-ai-lab")
    parser.add_argument("--threads", type=int, choices=range(1, 5), default=2)
    parser.add_argument("--llama-server", default=os.environ.get("ARANDU_LLAMA_SERVER") or shutil.which("llama-server"))
    args = parser.parse_args()
    if args.command == "install":
        install(args.models)
    else:
        from aiohttp import web
        web.run_app(create_app(args.models, args.llama_server, args.threads), host="127.0.0.1", port=PORT, access_log=None)

if __name__ == "__main__":
    main()
