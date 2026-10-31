"""Loopback-only service. No HA credentials, execution, or implicit downloads."""
from __future__ import annotations
import argparse
import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
from .contracts import MODELS, candidates, digest, settings
from .adapter import Encoder
from .inference_slot import inference_slot

KEY = "e5-small"
PORT = 11557
REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"

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

def retrieve(text, catalog):
    """Standalone lexical retrieval visits every eligible pair; never first-N catalog."""
    import re
    import unicodedata
    def words(value):
        value = unicodedata.normalize("NFKD", value.casefold())
        return set(re.findall(r"[a-z0-9]+", "".join(c for c in value if not unicodedata.combining(c)))) - {"a", "o", "da", "do", "de", "na", "no", "e"}
    query = words(text)
    ranking = [{**r, "raw_score": len(query & words(r["text"])) / max(1, len(query))} for r in candidates(catalog)]
    ranking = sorted(ranking, key=lambda r: (-r["raw_score"], r["entity_id"], r["intent"]))
    return {"ranking": [r for r in ranking if r["raw_score"] > 0][:8], "retrieval": "lexical-full-catalog", "eligible_pairs": len(ranking)}

def create_app(root, executable=None, threads=2, runtime_settings=None):
    from aiohttp import web
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false")
    os.environ.update(OMP_NUM_THREADS=str(threads), OPENBLAS_NUM_THREADS=str(threads),
                      MKL_NUM_THREADS=str(threads), OMP_WAIT_POLICY='PASSIVE', KMP_BLOCKTIME='0')
    adapter = Encoder(root, threads)
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
        except (ValueError, KeyError, TypeError, OSError) as exc:
            import logging
            logging.getLogger('arandu.decision').exception('Falha na solicitação de inferência/configuração')
            return web.json_response({"error": "Falha no módulo; consulte os logs.", "error_type": type(exc).__name__,
                "phase": getattr(adapter, "phase", "validation")}, status=422)

    app = web.Application(client_max_size=2_097_152, middlewares=[boundary])
    async def status(request):
        installed = (root / KEY / "arandu-model.json").is_file() and (root / KEY / "model.safetensors").is_file()
        available = installed and True
        return web.json_response({"package": "arandu-decision", "version": "0.3.0", "protocol": "arandu-inference-v1",
            "models": [{"id": KEY, **MODELS[KEY], "installed": installed, "available": bool(available),
                "reason": None if available else "Pesos ou runtime ausentes"}], "busy": lock.locked(), "threads": threads})

    async def infer(request):
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("Objeto JSON obrigatório")
        action = body.get("action", "decision")
        if body.get("model", KEY) != KEY and action != "llm":
            raise ValueError("Modelo não pertence a este pacote")
        if lock.locked():
            return web.json_response({"error": "Serviço ocupado"}, status=429)
        async def work():
            async with lock, inference_slot():
                if action == "unload":
                    adapter.unload()
                    return {"unloaded": True}
                if action not in {"decision", "load", "calibrate", "readiness"}:
                    raise ValueError("Ação indisponível neste pacote")
                if action == "load":
                    return {"model_fingerprint": await asyncio.to_thread(adapter.load, KEY), "load_ms": adapter.load_ms}
                catalog = validate_catalog(body.get("catalog"))
                if action == "readiness":
                    return adapter.readiness(catalog)
                if action == "calibrate":
                    return await asyncio.to_thread(adapter.calibrate, catalog, KEY, body["train"], body["validation"])
                text = body.get("text")
                if not isinstance(text, str) or not 1 <= len(text) <= 512 or any(ord(c) < 32 for c in text):
                    raise ValueError("Texto deve ter 1–512 caracteres em uma linha")
                config = dict(body.get('config') or {})
                config.update({k: v for k, v in (runtime_settings or {}).items() if k in {'threshold', 'min_margin'}})
                config['threshold'], config['min_margin'] = settings(config)
                result = await asyncio.to_thread(adapter.infer, text, catalog, KEY, config, context=body.get("context"))
                result['applied_config'] = config
                result.update(catalog_fingerprint=digest(catalog), catalog_entities=len(catalog["entities"]), service="arandu-decision")
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
        adapter.unload()
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
