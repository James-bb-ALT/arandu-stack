"""Local desktop entry point reusing the HA panel and real Rust/neural APIs. Dry-run only."""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from custom_components.local_nlu.ai_lab import local_request
from custom_components.local_nlu.ai_lab_core import benchmark, digest, pipeline, validate_proposal
from custom_components.local_nlu.client import LocalNluClient
from custom_components.local_nlu.contextual_protocol import parse


def create_app(worker="http://127.0.0.1:11556", nlu_endpoint="http://127.0.0.1:11555"):
    from aiohttp import web, ClientSession
    catalog = {"version": 4, "entities": [], "areas": [], "groups": []}
    catalog["generation"] = digest(catalog)
    lock = asyncio.Lock()

    @web.middleware
    async def boundary(request, handler):
        if request.host not in {"127.0.0.1:8766", "localhost:8766"}:
            raise web.HTTPForbidden()
        if request.method == "POST" and (request.headers.get("X-Arandu-Lab-UI") != "1" or request.headers.get("Origin") not in (None, "http://127.0.0.1:8766", "http://localhost:8766")):
            raise web.HTTPForbidden()
        try:
            return await handler(request)
        except (ValueError, KeyError, TypeError, OSError, TimeoutError) as exc:
            return web.json_response({"error": str(exc)[:512]}, status=422)

    app = web.Application(client_max_size=2_097_152, middlewares=[boundary])

    async def home(_):
        return web.Response(text='''<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Arandu AI Lab</title>
<style>body{margin:0;font:14px system-ui;background:#f4f6f3;color:#172a27}.connection{padding:16px 24px;background:#e5eee8;border-bottom:1px solid #b8ccbf}.connection summary{cursor:pointer}.connection input{margin:8px;padding:8px}#notice{margin:4px 0}</style>
<section class="connection"><strong>Arandu AI Lab · neste PC · inferência real · dry-run</strong><p id="notice">Home Assistant não conectado. Catálogo vazio: nenhum dispositivo será inventado ou acionado.</p>
<details><summary>Usar catálogo real do Home Assistant</summary><p>Importe o JSON exportado pelo AI Lab do HA. O snapshot fica somente na memória deste processo. Para execução real, use o painel da integração dentro do Home Assistant.</p><label>Catálogo JSON <input id="catalogFile" type="file" accept="application/json,.json"></label><p id="importStatus"></p></details></section>
<arandu-ai-lab></arandu-ai-lab><script type="module">
import '/panel.js';
const panel=document.querySelector('arandu-ai-lab');
async function call(payload){const response=await fetch('/api/lab',{method:'POST',headers:{'Content-Type':'application/json','X-Arandu-Lab-UI':'1'},body:JSON.stringify(payload)});const result=await response.json();if(!response.ok)throw Error(result.error||'Falha local');return result;}
panel.hass={callWS:request=>call(request.payload)};
const real=panel.shadowRoot.getElementById('real');real.disabled=true;real.title='Execução real somente no painel dentro do Home Assistant';
document.getElementById('catalogFile').onchange=async event=>{try{const file=event.target.files[0];if(!file||file.size>2000000)throw Error('Limite 2 MB');const result=await call({command:'import_catalog',catalog:JSON.parse(await file.text())});document.getElementById('notice').textContent=`Catálogo importado: ${result.entities} entidades. Snapshot de teste; execução real bloqueada.`;document.getElementById('importStatus').textContent='Importado na memória.';}catch(error){document.getElementById('importStatus').textContent=error.message;}};
</script></html>''', content_type="text/html", charset="utf-8")

    async def api(request):
        nonlocal catalog
        payload = await request.json()
        command = payload.get("command", "run")
        if command == "execute":
            raise ValueError("Aplicativo local é dry-run. Use o executor da integração no Home Assistant.")
        if lock.locked():
            raise ValueError("Teste em andamento; aguarde")
        async with lock, ClientSession() as session:
            if command in {"demo_suite", "demo_case"}:
                from custom_components.local_nlu.ai_lab_demo import suite, run_case
                if command == "demo_suite":
                    return web.json_response(suite())
                async def demo_infer(body):
                    return await local_request(session, worker, "/infer", payload=body)
                return web.json_response(await run_case(payload, LocalNluClient(session, nlu_endpoint), demo_infer))
            if command == "import_catalog":
                value = payload["catalog"]
                if type(value) is not dict or value.get("version") != 4 or type(value.get("entities")) is not list or len(value["entities"]) > 8192 or type(value.get("areas")) is not list:
                    raise ValueError("Use um snapshot v4 exportado pela integração")
                value = {k: value.get(k, []) for k in ("entities", "areas", "groups")}
                value["version"] = 4
                # State is UI-only metadata; preserve the production wire descriptor schema.
                value["entities"] = [{k: v for k, v in row.items() if k != "state"} for row in value["entities"]]
                value["generation"] = digest(value)
                response = await LocalNluClient(session, nlu_endpoint).async_catalog_v4(value)
                if parse(response).status != "catalog_ready":
                    raise ValueError("Rust rejeitou o catálogo")
                catalog = value
                return web.json_response({"entities": len(value["entities"])})
            snapshot = copy.deepcopy(catalog)
            if command == "status":
                return web.json_response(await local_request(session, worker, "/status", timeout=5))
            if command == "catalog":
                return web.json_response(snapshot)
            if command in {"load", "unload", "install", "calibrate"}:
                return web.json_response(await local_request(session, worker, "/infer", payload={**payload, "action": command, "catalog": snapshot}, timeout=180))
            if command in {"transcribe", "tts"}:
                # Proxy existing audio APIs; no alternative STT/TTS implementation.
                if command == "transcribe":
                    import base64
                    audio = base64.b64decode(payload["audio"], validate=True)
                    raw = await local_request(session, payload["stt_endpoint"], "/api/compare-upload", body=audio)
                    result = raw.get(payload.get("stt_model", "original"))
                    if not result:
                        raise ValueError("STT escolhido indisponível")
                    return web.json_response({**result, "model": payload.get("stt_model", "original")})
                import base64, time
                before = time.perf_counter()
                audio, _ = await local_request(session, payload["tts_endpoint"], "/api/synthesize", payload={"text": payload["text"], "engine": payload.get("tts_model", "piper")}, headers={"X-Arandu-UI": "1", "X-Arandu-Token": payload.get("tts_token", "")}, binary=True)
                return web.json_response({"audio": base64.b64encode(audio).decode(), "model": payload.get("tts_model", "piper"), "latency_ms": (time.perf_counter() - before) * 1000})

            async def run(text, model):
                async def nlu(value):
                    client = LocalNluClient(session, nlu_endpoint)
                    await client.async_catalog_v4(snapshot)
                    raw = await client.async_interpret_v4({"version": 4, "generation": snapshot["generation"], "text": value,
                        "origin_area": None, "origin_area_source": "unknown", "last_area": None, "last_targets": [], "pending": None})
                    parse(raw)
                    return raw
                async def decision(data):
                    return await local_request(session, worker, "/infer", payload={**data, "action": "decision", "catalog": snapshot,
                        "model": model, "config": {"threshold": payload.get("threshold", .5), "min_margin": payload.get("min_margin", .08)}})
                async def llm(data):
                    raw = await local_request(session, worker, "/infer", payload={**data, "action": "llm", "llm_model": payload.get("llm_model", "tucano"), "catalog": snapshot})
                    validate_proposal(raw["proposal"], snapshot, {r["entity_id"] for r in (data.get("decision") or {}).get("ranking", [])})
                    return raw
                result = await pipeline(text, nlu, decision, llm, mode=payload.get("mode", "pipeline"), timeout=95)
                proposal = result["proposal"]
                result.update(dry_run=True, validation_scope="imported_snapshot_only; no live HA authorization", catalog_generation=snapshot["generation"], operations_validated=False)
                if proposal:
                    result["action_json"] = proposal.get("operations", proposal)
                return result
            if command == "run":
                return web.json_response(await run(payload["text"], payload.get("model", "e5-small")))
            if command == "compare":
                models, examples = payload["models"], payload["examples"]
                if not 1 <= len(models) <= 4 or not 1 <= len(examples) <= 20:
                    raise ValueError("Use 1–4 motores e 1–20 exemplos")
                output = {}
                for model in models:
                    rows = [{"result": await run(e["text"], model), "expected": e.get("expected"), "actual": None} for e in examples]
                    metrics = benchmark(rows)
                    # No live HA validator here: never report snapshot previews as measured execution accuracy.
                    metrics.update(accuracy=None, false_activations=None, device_errors=None)
                    output[model] = {"rows": rows, "metrics": metrics}
                return web.json_response({"models": output, "dry_run": True, "sequential": True, "catalog_generation": snapshot["generation"]})
            raise ValueError("Comando desconhecido")

    async def panel(_):
        return web.FileResponse(ROOT / "custom_components/local_nlu/ai_lab_panel.js")
    app.router.add_get("/", home)
    app.router.add_get("/panel.js", panel)
    app.router.add_post("/api/lab", api)
    return app


if __name__ == "__main__":
    from aiohttp import web
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", default="http://127.0.0.1:11556")
    parser.add_argument("--nlu", default="http://127.0.0.1:11555")
    args = parser.parse_args()
    web.run_app(create_app(args.worker, args.nlu), host="127.0.0.1", port=8766, access_log=None)
