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
from .contracts import candidates, digest
from .models import MODELS, installed, install, validate_generation
from .adapter import Llama, LlmOutputError
from .inference_slot import inference_slot
from .semantic_context import resolve_device, decomposition, clearly_conversational

KEY = "tucano"
PORT = 11558
REVISION = "cbb5318b66a49e20967623e946365bcc8594c29a"


def validate_text(text):
    if type(text) is not str or not 1<=len(text)<=512 or any(ord(c)<32 for c in text):
        raise ValueError('Texto deve ter 1–512 caracteres em uma linha')
    return text

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
        if len(row['name'])>256 or len(row.get('aliases',[]))>32 or any(len(n)>256 for n in row.get('aliases',[])):
            raise ValueError('Descritores excedem limite')
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
    from aiohttp import web, ClientError
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false")
    os.environ.update(OMP_NUM_THREADS=str(threads), OPENBLAS_NUM_THREADS=str(threads),
                      MKL_NUM_THREADS=str(threads), OMP_WAIT_POLICY='PASSIVE', KMP_BLOCKTIME='0')
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
        except (ValueError, KeyError, TypeError, OSError, ClientError) as exc:
            import logging
            logging.getLogger('arandu.llm').exception('Falha na solicitação de inferência/configuração')
            return web.json_response({"error": "Falha no módulo; consulte os logs.", "error_type": type(exc).__name__,
                "phase": getattr(adapter, "phase", "validation")}, status=422)

    app = web.Application(client_max_size=2_097_152, middlewares=[boundary])
    async def status(request):
        rows=[]
        for key,info in MODELS.items():
            present=installed(root,key)
            loaded=adapter.active_model==key and adapter.process is not None and adapter.process.returncode is None
            rows.append({'id':key,**info,'installed':present,'available':present and bool(executable and Path(executable).is_file()),
                'loaded':loaded,'integrity_verified':loaded and (getattr(adapter,'_model_identity',()) or ())[:3] in getattr(adapter,'verified_models',set()),'selected':key==adapter.settings.get('llm_model','tucano'),
                'reason':None if present else 'Pesos ausentes; instalação explícita necessária'})
        return web.json_response({"package": "arandu-llm", "version": "2.0.0", "protocol": "arandu-inference-v1",
            'models':rows,'active_model':adapter.active_model if adapter.process and adapter.process.returncode is None else None,'profiles':adapter.settings.get('profiles',{}),
            'contract_version':adapter.settings.get('contract_version',1),"busy": lock.locked(), "threads": adapter.threads})

    async def infer(request):
        received=time.perf_counter()
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("Objeto JSON obrigatório")
        action = body.get("action", "llm")
        selected=body.get('llm_model',adapter.settings.get('llm_model',KEY))
        if selected not in MODELS:raise ValueError('Modelo LLM desconhecido')
        if lock.locked() or tasks:
            return web.json_response({"error": "Serviço ocupado"}, status=429)
        enqueued=time.perf_counter()
        async def work():
            if action=='install':
                async with lock:return await asyncio.to_thread(install,root,selected)
            async with lock, inference_slot():
                acquired=time.perf_counter()
                if action == "unload":
                    await adapter.unload()
                    return {"unloaded": True}
                if action in {'load','apply'}:
                    from .structured import ensure
                    old_settings=dict(adapter.settings);old_threads=adapter.threads;previous=adapter.active_model
                    try:
                        if action=='apply':
                            generation=validate_generation(body.get('generation',{}))
                            adapter.settings.update(generation,llm_model=selected,contract_version=body.get('contract_version',2))
                            if type(adapter.settings['contract_version']) is not int or adapter.settings['contract_version'] not in (1,2):raise ValueError('Contrato LLM inválido')
                            adapter.threads=generation.get('threads',adapter.threads)
                            profile={k:(adapter.threads if k=='threads' else adapter.settings[k]) for k in ('threads','context_size','max_tokens','temperature')}
                            profiles=dict(adapter.settings.get('profiles',{}));profiles[selected]=profile
                            adapter.settings['profiles']=profiles
                        load_ms=await ensure(adapter,selected)
                        return {'loaded':True,'model':selected,'load_ms':load_ms,'integrity_verified':True}
                    except BaseException:
                        adapter.settings=old_settings;adapter.threads=old_threads
                        if previous:
                            try:await ensure(adapter,previous)
                            except Exception:pass
                        raise
                if action=='llm_compare':
                    models=body.get('models')
                    if type(models) is not list or not 1<=len(models)<=2 or len(set(models))!=len(models) or any(k not in MODELS for k in models):
                        raise ValueError('Selecione 1–2 modelos LLM conhecidos')
                    catalog=validate_catalog(body.get('catalog'));text=validate_text(body.get('text'))
                    results=[];previous=adapter.active_model;previous_settings=dict(adapter.settings);previous_threads=adapter.threads
                    try:
                        for key in models:
                            try:results.append(await adapter.infer({**body,'text':text,'llm_model':key,'contract_version':2},catalog))
                            except Exception as exc:
                                results.append({'model':key,'kind':'error','error_type':type(exc).__name__,'reason':'model_or_generation_unavailable'})
                    finally:
                        adapter.settings=previous_settings;adapter.threads=previous_threads
                        if previous:
                            from .structured import ensure
                            await ensure(adapter,previous)
                        else:await adapter.unload()
                    return {'comparisons':results,'dry_run':True,'proposal':None,'home_assistant_actions':0,'queue_ms':(acquired-enqueued)*1000}
                if action not in {"llm"}:
                    raise ValueError("Ação indisponível neste pacote")
                catalog = validate_catalog(body.get("catalog"))
                text = validate_text(body.get('text'))
                before = time.perf_counter()
                if 'contract_version' not in body:
                    accepted=body.get('accept_contracts',[1])
                    if type(accepted) is not list or not 1<=len(accepted)<=2 or any(type(v) is not int or v not in (1,2) for v in accepted):
                        raise ValueError('Negociação de contrato inválida')
                    version=adapter.settings.get('contract_version',1)
                    if version not in accepted:
                        if 1 in accepted and selected=='tucano':version=1
                        else:raise ValueError('Cliente precisa suportar contrato LLM 2')
                    body['contract_version']=version
                if body.get('contract_version',adapter.settings.get('contract_version',1))==2:
                    result=await adapter.infer({**body,'text':text,'llm_model':selected},catalog)
                    result.update(service='arandu-llm',catalog_entities=len(catalog['entities']),queue_ms=(acquired-enqueued)*1000,
                        request_total_ms=(time.perf_counter()-received)*1000)
                    return result
                if selected!='tucano':raise ValueError('Contrato legado suporta somente Tucano')
                if clearly_conversational(text, catalog):
                    result = await adapter.infer({**body, 'llm_model': KEY, 'decision': {}}, catalog)
                    result.update(retrieval={'ranking': [], 'retrieval': 'general_conversation'}, retrieval_ms=0,
                                  catalog_fingerprint=digest(catalog), catalog_entities=len(catalog['entities']), service='arandu-llm')
                    return result
                external = body.get("decision") or {}
                resolved = resolve_device(text, catalog, body.get("context"))
                external_ids = {r["entity_id"] for r in external.get("ranking", [])}
                retrieval = external if external_ids and (resolved is None or resolved["entity_id"] in external_ids) else retrieve(text, catalog, body.get("context"))
                retrieval = {**retrieval, "analysis": external.get("analysis") or decomposition(text, catalog, body.get("context")),
                             "decision_status": external.get("decision_status"), "reason": external.get("reason"),
                             "confidence": external.get("confidence")}
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
            async with asyncio.timeout(360 if action=='install' else 180):
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

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["serve", "install"])
    parser.add_argument("--models", type=Path, default=Path.home() / ".cache" / "arandu-ai-lab")
    parser.add_argument("--threads", type=int, choices=range(1, 5), default=2)
    parser.add_argument('--model',choices=MODELS,default='tucano')
    parser.add_argument('--contract-version',type=int,choices=(1,2),default=2)
    parser.add_argument("--llama-server", default=os.environ.get("ARANDU_LLAMA_SERVER") or shutil.which("llama-server"))
    args = parser.parse_args()
    if args.command == "install":
        install(args.models,args.model)
    else:
        from aiohttp import web
        web.run_app(create_app(args.models, args.llama_server, args.threads,runtime_settings={'llm_model':args.model,'contract_version':args.contract_version}), host="127.0.0.1", port=PORT, access_log=None)

if __name__ == "__main__":
    main()
