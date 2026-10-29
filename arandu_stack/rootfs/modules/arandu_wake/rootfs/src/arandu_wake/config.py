"""Inventário declarativo; credenciais e estado ao vivo ficam fora dele."""

import ipaddress
import json
import math
import re
from pathlib import Path
from urllib.parse import urlsplit


def validate_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("HA_URL deve usar http:// ou https:// com hostname")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("HA_URL não pode conter credenciais, query ou fragmento")
    if parsed.path not in {"", "/"}:
        raise ValueError("HA_URL deve apontar à raiz do Home Assistant")
    if parsed.scheme == "http":
        host = parsed.hostname.lower()
        local = host == "localhost" or host.endswith(".local")
        try:
            ip = ipaddress.ip_address(host)
            local = local or ip.is_private or ip.is_loopback
        except ValueError:
            pass
        if not local:
            raise ValueError("Use HTTPS para HA fora de localhost, IP privado ou .local")
    return url.rstrip("/")


def validate_config(data: dict) -> list[str]:
    errors = []
    if not isinstance(data, dict):
        return ["Configuração deve ser objeto JSON"]
    if data.get("schema_version") != 1:
        errors.append("schema_version deve ser 1")
    ha = data.get("home_assistant")
    if not isinstance(ha, dict):
        errors.append("home_assistant deve ser objeto")
        ha = {}
    try:
        validate_url(ha.get("url", ""))
    except (ValueError, TypeError, AttributeError):
        errors.append("home_assistant.url inválida ou insegura")
    if not isinstance(ha.get("pipeline_name"), str) or not ha["pipeline_name"].strip():
        errors.append("Informe pipeline_name")
    if ha.get("pipeline_id") is not None and not isinstance(ha["pipeline_id"], str):
        errors.append("pipeline_id deve ser string ou null")
    engines = ha.get("expected_engines")
    if not isinstance(engines, dict):
        errors.append("expected_engines deve ser objeto")
    else:
        for key in ("stt_engine", "conversation_engine", "tts_engine"):
            if key not in engines or (engines[key] is not None and not isinstance(engines[key], str)):
                errors.append(f"expected_engines.{key} deve ser string ou null")
    if data.get("wake_word") != "alexa":
        errors.append("O MVP aceita somente wake_word alexa")
    satellites = data.get("satellites")
    if not isinstance(satellites, list):
        return errors + ["satellites deve ser lista"]
    seen = set()
    for index, satellite in enumerate(satellites):
        label = f"satellites[{index}]"
        if not isinstance(satellite, dict):
            errors.append(f"{label} deve ser objeto")
            continue
        ident = satellite.get("id")
        if not isinstance(ident, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", ident):
            errors.append(f"{label}.id inválido")
        elif ident in seen:
            errors.append(f"{label}.id duplicado")
        else:
            seen.add(ident)
        for key in ("name", "address", "room", "pipeline"):
            if not isinstance(satellite.get(key), str) or not satellite[key].strip():
                errors.append(f"{label}.{key} obrigatório")
        if satellite.get("pipeline") != ha.get("pipeline_name"):
            errors.append(f"{label}.pipeline diverge do pipeline comum")
        if satellite.get("type") not in {"esphome", "echomuse", "android", "wyoming"}:
            errors.append(f"{label}.type desconhecido")
        if satellite.get("mode") not in {"central", "local"}:
            errors.append(f"{label}.mode inválido")
        value = satellite.get("sensitivity")
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1):
            errors.append(f"{label}.sensitivity deve estar entre 0 e 1 ou null")
        for key in ("connection_entity", "listening_entity"):
            entity = satellite.get(key)
            if entity is not None and (not isinstance(entity, str) or not re.fullmatch(r"[a-z0-9_]+\.[a-z0-9_]+", entity)):
                errors.append(f"{label}.{key} inválida")
    return errors


def load_config(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    errors = validate_config(data)
    if errors:
        raise ValueError("; ".join(errors))
    return data
