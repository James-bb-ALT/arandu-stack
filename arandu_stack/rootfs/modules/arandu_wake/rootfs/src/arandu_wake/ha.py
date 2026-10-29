"""Acesso somente leitura à API oficial; sem registrar tokens ou transcrições."""

import json
from contextlib import contextmanager

from .config import validate_url


@contextmanager
def connect(url: str, token: str, timeout: float = 10):
    import websocket

    base = validate_url(url)
    if not token or not token.strip():
        raise ValueError("Defina HA_TOKEN no ambiente para diagnóstico autenticado")
    target = ("wss" if base.startswith("https:") else "ws") + base[base.index(":"):] + "/api/websocket"
    # Nenhum redirect é seguido: o token só vai ao servidor configurado.
    ws = websocket.create_connection(target, timeout=timeout, redirect_limit=0)
    try:
        if json.loads(ws.recv()).get("type") != "auth_required":
            raise ValueError("Handshake Home Assistant inesperado")
        ws.send(json.dumps({"type": "auth", "access_token": token}))
        if json.loads(ws.recv()).get("type") != "auth_ok":
            raise ValueError("Autenticação Home Assistant recusada")
        yield ws
    finally:
        ws.close()


def request(ws, ident: int, kind: str):
    ws.send(json.dumps({"id": ident, "type": kind}))
    for _ in range(100):
        message = json.loads(ws.recv())
        if message.get("id") != ident:
            continue
        if not message.get("success"):
            raise ValueError(f"API {kind} recusou a consulta")
        return message["result"]
    raise ValueError("API não retornou a resposta esperada")


def assess_pipeline(config: dict, pipelines: list[dict]) -> dict:
    ha = config["home_assistant"]
    ident = ha.get("pipeline_id")
    matches = [p for p in pipelines if (p.get("id") == ident if ident else p.get("name") == ha["pipeline_name"])]
    if len(matches) != 1:
        return {"ready": False, "issues": ["Pipeline ausente ou nome ambíguo; configure o ID exato"]}
    pipeline = matches[0]
    issues = []
    expected = ha["expected_engines"]
    for key in ("stt_engine", "conversation_engine", "tts_engine"):
        if not pipeline.get(key):
            issues.append(f"{key} ausente")
        elif not expected.get(key):
            issues.append(f"{key}: identidade Arandu ainda não confirmada em expected_engines")
        elif expected[key] != pipeline[key]:
            issues.append(f"{key} difere do motor esperado")
    if not pipeline.get("wake_word_entity") or (pipeline.get("wake_word_id") or "").casefold() != "alexa":
        issues.append("Pipeline sem wake word central Alexa")
    return {
        "ready": not issues,
        "pipeline_id": pipeline["id"],
        "pipeline_name": pipeline.get("name"),
        "engines": {key: pipeline.get(key) for key in ("stt_engine", "conversation_engine", "tts_engine")},
        "wake_word_id": pipeline.get("wake_word_id"),
        "issues": issues,
        "scope": "Configuração consultada; áudio, TTS no aparelho e sessão física não testados",
    }


def inspect(config: dict, url: str, token: str) -> dict:
    with connect(url, token) as ws:
        result = request(ws, 1, "assist_pipeline/pipeline/list")
        states = request(ws, 2, "get_states")
    report = assess_pipeline(config, result["pipelines"])
    by_entity = {s["entity_id"]: s for s in states}
    report["satellites"] = []
    for satellite in config["satellites"]:
        status = {"id": satellite["id"], "room": satellite["room"]}
        for key in ("connection_entity", "listening_entity"):
            entity = satellite.get(key)
            state = by_entity.get(entity)
            status[key] = {"entity_id": entity, "state": state["state"] if state else "unknown"}
        report["satellites"].append(status)
    return report
