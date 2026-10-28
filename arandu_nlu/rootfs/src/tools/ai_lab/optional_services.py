"""Optional sibling services; absence never loads their models inside NLU."""
from __future__ import annotations
import asyncio
from aiohttp import ClientSession, ClientTimeout, ClientError

SERVICES = {"e5-small": ("arandu-decision", "http://127.0.0.1:11557"),
            "tucano": ("arandu-llm", "http://127.0.0.1:11558")}

async def request(key, path, payload=None):
    package, base = SERVICES[key]
    try:
        async with ClientSession(timeout=ClientTimeout(total=185 if payload is not None else 2), trust_env=False) as session:
            async with session.request("POST" if payload is not None else "GET", base + path,
                    json=payload, headers={"X-Arandu-Lab": "1"}, allow_redirects=False) as response:
                raw = await response.content.read(2_097_153)
                if len(raw) > 2_097_152:
                    raise ValueError("Resposta do pacote excedeu limite")
                import json
                output = json.loads(raw)
                if response.status != 200:
                    raise ValueError(output.get("error", f"HTTP {response.status}"))
                return output
    except (ClientError, TimeoutError) as exc:
        raise ValueError(f"{package} indisponível; instale o pacote e inicie o serviço") from exc

async def statuses():
    async def one(key):
        try:
            status = await request(key, "/status")
            if status.get("package") != SERVICES[key][0] or status.get("protocol") != "arandu-inference-v1":
                raise ValueError("Serviço incompatível")
            return key, status["models"][0]
        except (ValueError, KeyError, IndexError):
            return key, {"installed": False, "available": False, "reason": SERVICES[key][0] + " ausente ou serviço indisponível"}
    return dict(await asyncio.gather(*(one(k) for k in SERVICES)))
