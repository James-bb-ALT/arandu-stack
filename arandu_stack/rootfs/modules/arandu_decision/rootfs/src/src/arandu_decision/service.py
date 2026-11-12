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
from .contracts import MODELS, selection, candidates, digest, settings
from .installation import install, installed
from .backends import gpu_probe, BackendUnavailable
from .adapter import Encoder
from .inference_slot import inference_slot

PORT = 11557

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
        if len(row['name']) > 256 or len(row.get('aliases', [])) > 32 or any(len(a) > 256 for a in row.get('aliases', [])):
            raise ValueError('Descritores excedem limites')
        for field in ('device_name','device_id','registry_id','device_class','area_id'):
            if row.get(field) is not None and (type(row[field]) is not str or len(row[field]) > 256):
                raise ValueError('Metadado de entidade inválido')
        if row.get('attributes',{}).get('semantic_category') is not None and type(row['attributes']['semantic_category']) is not str:
            raise ValueError('Categoria semântica inválida')
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
    active=dict(runtime_settings or {})
    active.setdefault("model","e5-small");active.setdefault("backend","cpu")
    active.setdefault('algorithm','baseline-v2')
    active['threshold'],active['min_margin']=settings(active)
    selection(active["model"],active["backend"])
    adapter.model_key=active["model"];adapter.backend_name=active["backend"]
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
        except BackendUnavailable as exc:
            return web.json_response({"error":str(exc),"code":"backend_unavailable","phase":"loading","decision_status":"error","proposal":None},status=422)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            import logging
            logging.getLogger('arandu.decision').exception('Falha na solicitação de inferência/configuração')
            return web.json_response({"error": "Falha no módulo; consulte os logs.", "error_type": type(exc).__name__,
                "phase": getattr(adapter, "phase", "validation"), 'decision_status':'error', 'proposal':None}, status=422)

    app = web.Application(client_max_size=2_097_152, middlewares=[boundary])
    preload_ms = 0.0
    async def warm(app):
        nonlocal preload_ms
        if installed(root,active['model']):
            try:
                async with inference_slot():
                    await asyncio.to_thread(adapter.load,active['model'],active['backend'])
                    preload_ms=adapter.load_ms;adapter.phase='ready'
            except Exception:
                import logging
                logging.getLogger('arandu.decision').exception('Pré-carga indisponível; fast-path permanece independente')
    app.on_startup.append(warm)
    async def status(request):
        gpu=await asyncio.to_thread(gpu_probe)
        rows=[]
        for key,info in MODELS.items():
            present=installed(root,key)
            loaded=adapter.model is not None and getattr(adapter,'active_model_key',None)==key
            rows.append({'id':key,**{k:v for k,v in info.items() if k!='files'},'installed':present,
                'available':present,'loaded':loaded,'selected':active['model']==key,
                'integrity_verified':loaded,'backend_used':adapter.active_backend if loaded else None,
                'reason':None if present else 'Pesos ausentes; instalação explícita necessária'})
        if adapter.model is not None and adapter.active_backend=='gpu_intel':
            gpu.update(validated=True,status='ready',execution_devices=getattr(adapter.model,'devices',[]))
        return web.json_response({'package':'arandu-decision','version':'3.0.0','protocol':'arandu-inference-v1',
            'models':rows,'backends':[{'id':'cpu','available':True,'status':'available','precision':'fp32'},gpu],
            'selected_model':active['model'],'selected_backend':active['backend'],
            'active_model':getattr(adapter,'active_model_key',None) if adapter.model is not None else None,
            'backend_used':adapter.active_backend,'loaded':adapter.model is not None,'busy':lock.locked() or bool(tasks),
            'config':{k:active[k] for k in ('algorithm','threshold','min_margin')},
            'threads':adapter.threads,'preload_ms':preload_ms,'deterministic_available':True})

    async def infer(request):
        received = time.perf_counter()
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("Objeto JSON obrigatório")
        action = "install" if request.path=="/install" else body.get("action", "decision")
        key,backend=selection(body.get("model",active["model"]),body.get("backend",active["backend"]))
        enqueued=time.perf_counter()
        if lock.locked() or tasks:
            return web.json_response({"error": "Serviço ocupado"}, status=429)
        async def work():
            async with lock:
                if action=="install":return await asyncio.to_thread(install,root,key)
                return await compute()
        async def compute():
            async with inference_slot():
                acquired=time.perf_counter()
                adapter.model_key=key;adapter.backend_name=backend
                if action == "unload":
                    adapter.unload()
                    return {"unloaded": True}
                if action not in {"decision", "load", "apply", "prepare", "compare", "calibrate", "readiness"}:
                    raise ValueError("Ação indisponível neste pacote")
                if action in {'load','apply'}:
                    previous=dict(active);old_threads=adapter.threads
                    previous_binding=(getattr(adapter,'active_model_key',None),adapter.active_backend) if adapter.model is not None else None
                    try:
                        proposed=body.get('config',{})
                        if type(proposed) is not dict or set(proposed)-{'algorithm','threshold','min_margin'}:raise ValueError('Política Decision inválida')
                        policy={**{k:active[k] for k in ('algorithm','threshold','min_margin')},**proposed}
                        if policy['algorithm'] not in {'baseline-v2','structured-v3'}:raise ValueError('Algoritmo Decision inválido')
                        policy['threshold'],policy['min_margin']=settings(policy)
                        value=body.get('threads',adapter.threads)
                        if type(value) is not int or not 1<=value<=4:raise ValueError('Threads inválidas')
                        if value!=adapter.threads:adapter.unload()
                        adapter.threads=value
                        bound=await asyncio.to_thread(adapter.load,key,backend)
                        if action=='apply':active.update(model=key,backend=backend,threads=value,**policy)
                        return {'model':key,'backend':backend,'backend_used':adapter.active_backend,'model_fingerprint':bound,
                            'loaded':True,'integrity_verified':True,'dimension':MODELS[key]['dimension'],'load_ms':adapter.load_ms,
                            'applied_config':{k:active[k] for k in ('algorithm','threshold','min_margin')}}
                    except BaseException:
                        adapter.threads=old_threads;active.clear();active.update(previous)
                        adapter.model_key=previous['model'];adapter.backend_name=previous['backend']
                        if previous_binding:
                            try:await asyncio.to_thread(adapter.load,*previous_binding)
                            except Exception:pass
                        raise
                if action=='prepare':
                    from .backends import prepare_ir
                    previous=(getattr(adapter,'active_model_key',None),adapter.active_backend)
                    adapter.unload()
                    try:return await asyncio.to_thread(prepare_ir,root,key)
                    finally:
                        if previous[0]:
                            try:await asyncio.to_thread(adapter.load,*previous)
                            except Exception:pass
                if action=='compare':
                    combos=body.get('combinations')
                    if type(combos) is not list or not 1<=len(combos)<=6:raise ValueError('Use 1–6 combinações')
                    pairs=[selection(c['model'],c['backend']) for c in combos if type(c) is dict]
                    if len(pairs)!=len(combos) or len(set(pairs))!=len(pairs):raise ValueError('Combinações inválidas/repetidas')
                    catalog=validate_catalog(body.get('catalog'))
                    examples=body.get('examples',[{'text':body.get('text')}]);repeats=body.get('repetitions',1);warmup=body.get('warmup',0)
                    if type(examples) is not list or not 1<=len(examples)<=20 or type(repeats) is not int or not 1<=repeats<=5 or type(warmup) is not int or not 0<=warmup<=2 or len(examples)*repeats+warmup>60:raise ValueError('Lote/repetições excedem limite')
                    for example in examples:
                        text=example.get('text') if type(example) is dict else None
                        if type(text) is not str or not 1<=len(text)<=512 or any(ord(c)<32 for c in text):raise ValueError('Texto inválido')
                    previous=(getattr(adapter,'active_model_key',None),adapter.active_backend)
                    results=[]
                    try:
                        for selected_model,selected_backend in pairs:
                            try:
                                if selected_backend=='gpu_intel' and not gpu_probe()['available']:raise BackendUnavailable(gpu_probe()['reason'])
                                adapter.backend_name=selected_backend
                                await asyncio.to_thread(adapter.load,selected_model,selected_backend)
                                cold_ms=adapter.load_ms
                                for iteration in range(warmup+len(examples)*repeats):
                                    warming=iteration<warmup;case=0 if warming else (iteration-warmup)%len(examples)
                                    before=time.perf_counter()
                                    row=await asyncio.to_thread(adapter.infer,examples[case]['text'],catalog,selected_model,
                                        {**active,'algorithm':'structured-v3','backend':selected_backend},context=body.get('context'))
                                    row.update(backend=selected_backend,backend_used=adapter.active_backend,combination=selected_model+':'+selected_backend,
                                        case=case,repetition=-1 if warming else (iteration-warmup)//len(examples),warmup=warming,
                                        cold_start_ms=cold_ms if iteration==0 else 0,wall_ms=(time.perf_counter()-before)*1000,
                                        dimension=MODELS[selected_model]['dimension'],**adapter.measurements)
                                    results.append(row)
                            except Exception as exc:
                                results.append({'model':selected_model,'backend':selected_backend,'combination':selected_model+':'+selected_backend,
                                    'decision_status':'unsupported' if isinstance(exc,BackendUnavailable) else 'error','proposal':None,
                                    'error_type':type(exc).__name__,'reason':str(exc)[:256] if isinstance(exc,BackendUnavailable) else 'model_or_runtime_unavailable'})
                    finally:
                        adapter.model_key=active['model'];adapter.backend_name=active['backend']
                        if previous[0]:await asyncio.to_thread(adapter.load,*previous)
                        else:adapter.unload()
                    return {'comparisons':results,'dry_run':True,'proposal':None,'home_assistant_actions':0,'queue_ms':(acquired-enqueued)*1000}

                catalog = validate_catalog(body.get("catalog"))
                algorithm = body.get('algorithm', (body.get('config') or {}).get('algorithm', active['algorithm']))
                if algorithm not in {'baseline-v2','structured-v3'}:
                    raise ValueError('Algoritmo Decision desconhecido')
                if action == "readiness":
                    return adapter.readiness(catalog, algorithm)
                if action == "calibrate":
                    return await asyncio.to_thread(adapter.calibrate, catalog, key, body["train"], body["validation"], algorithm=algorithm, provenance=body.get('provenance'))
                text = body.get("text")
                if not isinstance(text, str) or not 1 <= len(text) <= 512 or any(ord(c) < 32 for c in text):
                    raise ValueError("Texto deve ter 1–512 caracteres em uma linha")
                config = dict(body.get('config') or {})
                config['algorithm'] = algorithm
                config['backend']=backend
                config.update({k:active[k] for k in ('threshold','min_margin')})
                config['threshold'], config['min_margin'] = settings(config)
                queued = time.perf_counter()
                if body.get('compare_algorithms') is True:
                    comparisons = []
                    for selected in ('baseline-v2','structured-v3'):
                        comparisons.append(await asyncio.to_thread(adapter.infer, text, catalog, key, {**config,'algorithm':selected}, context=body.get('context')))
                    # Comparison is diagnostic. Never return an executable top-level proposal.
                    return {'algorithm_comparison':comparisons,'proposal':None,'decision_status':'rejected',
                            'reason':'diagnostic_comparison_only','dry_run':True,'catalog_fingerprint':digest(catalog)}
                result = await asyncio.to_thread(adapter.infer, text, catalog, key, config, context=body.get("context"))
                result.setdefault('queue_ms', (acquired-enqueued)*1000)
                result.update(backend=backend,backend_used=adapter.active_backend if result.get('embedding_used') else None,dimension=MODELS[key]['dimension'],**adapter.measurements)
                result['applied_config'] = config
                result.update(catalog_fingerprint=digest(catalog), catalog_entities=len(catalog["entities"]), service="arandu-decision")
                return result
        task = asyncio.create_task(work())
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        try:
            async with asyncio.timeout(960 if action=="install" else 300):
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
    app.router.add_post("/install",infer)
    return app

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["serve", "install", "prepare"])
    parser.add_argument("--models", type=Path, default=Path.home() / ".cache" / "arandu-ai-lab")
    parser.add_argument("--model",choices=MODELS,default="e5-small")
    parser.add_argument("--backend",choices=["cpu","gpu_intel"],default="cpu")
    parser.add_argument("--threads", type=int, choices=range(1, 5), default=2)
    parser.add_argument('--algorithm',choices=['baseline-v2','structured-v3'],default='structured-v3')
    parser.add_argument("--llama-server", default=os.environ.get("ARANDU_LLAMA_SERVER") or shutil.which("llama-server"))
    args = parser.parse_args()
    if args.command == "install":
        install(args.models,args.model)
    elif args.command=="prepare":
        from .backends import prepare_ir
        prepare_ir(args.models,args.model)
    else:
        from aiohttp import web
        web.run_app(create_app(args.models, args.llama_server, args.threads, runtime_settings={'algorithm':args.algorithm,'model':args.model,'backend':args.backend}), host="127.0.0.1", port=PORT, access_log=None)

if __name__ == "__main__":
    main()
