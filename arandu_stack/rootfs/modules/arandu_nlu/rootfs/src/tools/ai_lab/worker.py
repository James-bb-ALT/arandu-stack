"""Explicit local inference worker. Never connects to Home Assistant or executes effects."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))
from custom_components.local_nlu.ai_lab_core import MODELS
from adapters import Encoder, Llama
import optional_services


def install(root, key):
    from huggingface_hub import HfApi, snapshot_download, hf_hub_download
    if key not in MODELS:
        raise ValueError("Modelo sem adaptador validado")
    info = HfApi(token=False).model_info(MODELS[key]["repo"])
    directory = root / key
    if MODELS[key]["kind"] == "llm":
        hf_hub_download(info.id, MODELS[key]["file"], revision=info.sha, local_dir=directory, token=False)
    else:
        snapshot_download(info.id, revision=info.sha, local_dir=directory, token=False,
                          allow_patterns=["*.json", "*.safetensors", "*.txt", "*.model", "1_Pooling/*", "encoder/*.json", "tokenizer/*.json", "README.md", "LICENSE*", "ATTRIBUTIONS.md"])
    (directory / "arandu-model.json").write_text(json.dumps({"repo": info.id, "revision": info.sha, "license": MODELS[key]["license"]}), encoding="utf-8")
    print(json.dumps({"installed": key, "revision": info.sha, "path": str(directory)}))


def create_app(root, executable=None, threads=2):
    from aiohttp import web
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    encoder, llama = Encoder(root, threads), Llama(root, executable, threads)
    lock = asyncio.Lock()
    tasks = set()

    @web.middleware
    async def boundary(request, handler):
        # Reject browser-origin requests. Worker belongs behind trusted local access.
        if request.headers.get("Origin") or request.headers.get("X-Arandu-Lab") != "1":
            raise web.HTTPForbidden()
        try:
            return await handler(request)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            return web.json_response({"error": str(exc)[:256]}, status=422)

    app = web.Application(client_max_size=2_097_152, middlewares=[boundary])

    async def status(_):
        models = []
        optional = await optional_services.statuses()
        for key in sorted(MODELS, key=lambda k: (k not in {"e5-small", "tucano"}, k)):
            model = MODELS[key]
            installed = (root / key / "arandu-model.json").is_file()
            available = installed and not model.get("unavailable") and (model["kind"] != "llm" or bool(executable))
            models.append({"id": key, **model, "installed": installed, "available": bool(available),
                           "reason": model.get("unavailable") or (None if available else "Pesos ou runtime não instalados")})
            if key in optional:
                models[-1].update(optional[key])
        return web.json_response({"models": models, "busy": lock.locked(), "threads": threads, "resident": encoder.key})

    async def infer(request):
        payload = await request.json()
        if lock.locked():
            return web.json_response({"error": "Modelo ocupado; aguarde a inferência anterior"}, status=429)
        async def work():
            async with lock:
                action = payload.get("action", "decision")
                key = payload.get("llm_model", "tucano") if action == "llm" else payload.get("model", "e5-small")
                if action == "unload":
                    for optional_key, info in (await optional_services.statuses()).items():
                        if info.get("available"):
                            await optional_services.request(optional_key, "/infer", {"action": "unload"})
                if key in optional_services.SERVICES and action not in {"install", "unload"}:
                    return await optional_services.request(key, "/infer", payload)
                if action == "install":
                    if payload.get("model") not in MODELS:
                        raise ValueError("Modelo não homologado para instalação")
                    env = {**os.environ, "HF_HUB_OFFLINE": "0", "TRANSFORMERS_OFFLINE": "0"}
                    process = await asyncio.create_subprocess_exec(sys.executable, str(Path(__file__).resolve()), "install", "--models", str(root), "--model", payload["model"], env=env,
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                    async with asyncio.timeout(900):
                        try:
                            code = await process.wait()
                        except BaseException:
                            if process.returncode is None:
                                process.kill()
                                await process.wait()
                            raise
                    if code:
                        raise ValueError("Falha ao instalar; execute o comando install no host para diagnóstico")
                    return {"installed": payload["model"]}
                if action == "load":
                    signature = await asyncio.to_thread(encoder.load, payload["model"])
                    return {"model_fingerprint": signature, "load_ms": encoder.load_ms}
                if action == "unload":
                    encoder.unload()
                    await llama.unload()
                    return {"unloaded": True}
                if action == "llm":
                    encoder.unload()
                    return await llama.infer(payload, payload["catalog"])
                if action == "calibrate":
                    return await asyncio.to_thread(encoder.calibrate, payload["catalog"], payload["model"], payload["train"], payload["validation"])
                if action != "decision":
                    raise ValueError("Ação do worker inválida")
                return await asyncio.to_thread(encoder.infer, payload["text"], payload["catalog"], payload["model"], payload.get("config", {}))
        task = asyncio.create_task(work())
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        # Shield preserves serialization even when HTTP times out while native inference continues.
        try:
            async with asyncio.timeout(180):
                result = await asyncio.shield(task)
            return web.json_response(result)
        except TimeoutError:
            return web.json_response({"error": "Timeout; worker continua ocupado até finalizar com segurança"}, status=504)

    async def cleanup(_):
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await llama.unload()
        encoder.unload()
    app.on_cleanup.append(cleanup)
    app.router.add_get("/status", status)
    app.router.add_post("/infer", infer)
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["install", "serve"])
    parser.add_argument("--model", choices=list(MODELS), default="brazembed")
    parser.add_argument("--models", type=Path, default=Path.home() / ".cache" / "arandu-ai-lab")
    parser.add_argument("--llama-server", default=shutil.which("llama-server"))
    parser.add_argument("--threads", type=int, choices=range(1, 5), default=2)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11556)
    args = parser.parse_args()
    args.models.mkdir(parents=True, exist_ok=True)
    if args.command == "install":
        install(args.models, args.model)
    else:
        from aiohttp import web
        web.run_app(create_app(args.models, args.llama_server, args.threads), host=args.host, port=args.port, access_log=None)
