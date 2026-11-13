"""Generic typed capability-aware QuerySet engine over authorized HA catalog."""
from __future__ import annotations

import re
import unicodedata
from typing import Any


from .generated_semantics_ptbr import GROUP_FORMS, SURFACE_TO_CONCEPTS

_CONCEPT_TO_HA_DOMAIN: dict[str, str] = {
    "entity.lighting": "light",
    "entity.fan": "fan",
    "entity.switch": "switch",
    "entity.outlet": "switch",
    "entity.lock": "lock",
    "entity.cover_generic": "cover",
    "entity.curtain": "cover",
    "entity.blind": "cover",
    "entity.garage": "cover",
    "entity.contact_sensor": "binary_sensor",
    "entity.climate_unit": "climate",
    "entity.thermostat": "climate",
}


def _tokens_from_semantics(domain: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
    collected = set(fallback)
    for surface, concepts in SURFACE_TO_CONCEPTS.items():
        if any(_CONCEPT_TO_HA_DOMAIN.get(c) == domain for c in concepts):
            collected.add(surface)
    return tuple(sorted(collected, key=len, reverse=True))


def _norm(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text or "")
    plain = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", plain.lower().strip())


_DOMAIN_SPECS: dict[str, dict[str, Any]] = {
    "light": {
        "tokens": _tokens_from_semantics("light", ("luz", "luzes", "lampada", "lampadas", "luminaria", "luminarias", "iluminacao")),
        "active_states": ("on",),
        "inactive_states": ("off",),
        "plural": ("luz autorizada", "luzes autorizadas"),
        "active_label": ("acesa", "acesas"),
        "inactive_label": ("apagada", "apagadas"),
    },
    "fan": {
        "tokens": _tokens_from_semantics("fan", ("ventilador", "ventiladores", "ventoinha", "ventoinhas", "exaustor", "exaustores")),
        "active_states": ("on",),
        "inactive_states": ("off",),
        "plural": ("ventilador autorizado", "ventiladores autorizados"),
        "active_label": ("ligado", "ligados"),
        "inactive_label": ("desligado", "desligados"),
    },
    "switch": {
        "tokens": _tokens_from_semantics("switch", ("tomada", "tomadas", "interruptor", "interruptores")),
        "active_states": ("on",),
        "inactive_states": ("off",),
        "plural": ("dispositivo autorizado", "dispositivos autorizados"),
        "active_label": ("ligado", "ligados"),
        "inactive_label": ("desligado", "desligados"),
    },
    "lock": {
        "tokens": _tokens_from_semantics("lock", ("fechadura", "fechaduras", "tranca", "trancas")),
        "active_states": ("unlocked", "open"),
        "inactive_states": ("locked",),
        "plural": ("fechadura autorizada", "fechaduras autorizadas"),
        "active_label": ("destrancada", "destrancadas"),
        "inactive_label": ("trancada", "trancadas"),
    },
    "cover": {
        "tokens": _tokens_from_semantics("cover", ("cortina", "cortinas", "persiana", "persianas", "portao", "portoes")),
        "active_states": ("open", "opening"),
        "inactive_states": ("closed", "closing"),
        "plural": ("abertura autorizada", "aberturas autorizadas"),
        "active_label": ("aberta", "abertas"),
        "inactive_label": ("fechada", "fechadas"),
    },
    "binary_sensor": {
        "tokens": _tokens_from_semantics("binary_sensor", ("porta", "portas", "janela", "janelas", "sensor de porta", "sensor de janela")),
        "active_states": ("on", "open"),
        "inactive_states": ("off", "closed"),
        "plural": ("sensor autorizado", "sensores autorizados"),
        "active_label": ("aberta", "abertas"),
        "inactive_label": ("fechada", "fechadas"),
    },
    "climate": {
        "tokens": _tokens_from_semantics("climate", ("ar condicionado", "ar-condicionado", "termostato", "split", "climatizador")),
        "active_states": ("cool", "heat", "heat_cool", "auto", "dry", "fan_only", "on"),
        "inactive_states": ("off",),
        "plural": ("climatizador autorizado", "climatizadores autorizados"),
        "active_label": ("ligado", "ligados"),
        "inactive_label": ("desligado", "desligados"),
    },
}

_SEMANTIC_QUERY_FORMS = tuple(sorted(set(GROUP_FORMS.get("query", ()))))
_QUERY_PREFIX_RE = re.compile(
    r"\b(?:quantos?|quantas?|qua(?:l|is)|tem\s+algum(?:a)?|algum(?:a)?\s+\w+\s+(?:aces[ao]|ligad[ao]|abert[ao]|fechad[ao]|destrancad[ao]|trancad[ao]))\b"
)


def detect_queryset_domain(text: str) -> str | None:
    norm = _norm(text)
    if not (_QUERY_PREFIX_RE.search(norm) or any(re.search(rf"\b{re.escape(qf)}\b", norm) for qf in _SEMANTIC_QUERY_FORMS)):
        return None
    for domain, spec in _DOMAIN_SPECS.items():
        if any(re.search(rf"\b{re.escape(tok)}\b", norm) for tok in spec["tokens"]):
            return domain
    return None


def _extract_area_filter(norm_text: str, catalog: dict[str, Any]) -> tuple[str | None, str | None, bool]:
    if re.search(r"\b(?:na\s+casa(?:\s+inteira)?|em\s+casa|toda\s+a?\s*casa)\b", norm_text):
        return None, None, False
    areas = catalog.get("areas") or []
    best: tuple[int, str, str] | None = None
    for area in areas:
        area_id = area.get("area_id")
        names = [area_id] + list(area.get("names") or [])
        for name in names:
            if not isinstance(name, str) or not name.strip():
                continue
            n_name = _norm(name)
            if re.search(rf"\b(?:na|no|da|do|em)\s+{re.escape(n_name)}\b", norm_text):
                if best is None or len(n_name) > best[0]:
                    label = str(area.get("names", [area_id])[0] if area.get("names") else area_id)
                    best = (len(n_name), area_id, label)
    if best:
        return best[1], best[2], False
    m = re.search(r"\b(?:na|no)\s+([a-z0-9_-]{3,})\b", norm_text)
    if m and m.group(1) not in ("casa", "residencia"):
        return None, m.group(1), True
    return None, None, False


def _read_state(row: dict[str, Any], hass: Any | None, states_map: dict[str, str] | None) -> str:
    eid = row.get("entity_id") or ""
    if states_map is not None and eid in states_map:
        return str(states_map[eid]).lower()
    if hass is not None and hasattr(hass, "states"):
        st = hass.states.get(eid)
        if st is not None:
            return str(getattr(st, "state", "unknown")).lower()
    attrs = row.get("attributes") or {}
    if isinstance(attrs, dict) and attrs.get("state") is not None:
        return str(attrs["state"]).lower()
    return "unknown"


def evaluate_queryset(
    text: str,
    snapshot: Any,
    hass: Any | None = None,
    *,
    states_map: dict[str, str] | None = None,
    domain_override: str | None = None,
) -> dict[str, Any] | None:
    """Evaluate a read-only authorized QuerySet aggregation across any supported domain."""
    domain = domain_override or detect_queryset_domain(text)
    if not domain or domain not in _DOMAIN_SPECS:
        return None
    spec = _DOMAIN_SPECS[domain]
    norm = _norm(text)
    payload = getattr(snapshot, "payload", None) or {}
    by_id = getattr(snapshot, "by_id", None)
    entities = list(by_id.values()) if isinstance(by_id, dict) else list(payload.get("entities") or [])

    area_id, area_label, missing_area = _extract_area_filter(norm, payload)
    if missing_area:
        return {
            "domain": domain,
            "speech": f"Não encontrei dispositivos autorizados na área {area_label}.",
            "count_active": 0,
            "count_inactive": 0,
            "count_unavailable": 0,
            "total_authorized": 0,
            "mode": "area_not_found",
        }

    seen: dict[str, dict[str, Any]] = {}
    for row in entities:
        row_domain = row.get("domain")
        # Allow door/window binary_sensor or cover matching cleanly
        if domain == "binary_sensor" and row_domain not in ("binary_sensor", "cover"):
            continue
        if domain != "binary_sensor" and row_domain != domain:
            continue
        if area_id is not None and row.get("area_id") != area_id:
            continue
        eid = row.get("entity_id")
        if isinstance(eid, str) and eid not in seen:
            seen[eid] = row

    if not seen:
        return {
            "domain": domain,
            "speech": f"Não há {spec['plural'][1]} disponíveis para essa consulta.",
            "count_active": 0,
            "count_inactive": 0,
            "count_unavailable": 0,
            "total_authorized": 0,
            "mode": "empty",
        }

    active_names: list[str] = []
    count_active = 0
    count_inactive = 0
    count_unavailable = 0
    for row in seen.values():
        st = _read_state(row, hass, states_map)
        if st in spec["active_states"]:
            count_active += 1
            active_names.append(str(row.get("name") or row.get("entity_id")))
        elif st in spec["inactive_states"]:
            count_inactive += 1
        else:
            count_unavailable += 1

    scope_desc = f" em {area_label}" if area_label else ""
    total = len(seen)
    act_s, act_p = spec["active_label"]
    inact_p = spec["inactive_label"][1]
    if count_active == 0:
        speech = f"Nenhuma unidade está {spec['active_label'][0]}{scope_desc} ({count_inactive} {inact_p}, {total} no total)."
    elif count_active == 1:
        speech = f"Há 1 {act_s}{scope_desc}: {active_names[0]}."
    else:
        listed = ", ".join(active_names[:6])
        extra = f" e mais {count_active - 6}" if count_active > 6 else ""
        speech = f"Há {count_active} {act_p}{scope_desc}: {listed}{extra}."

    return {
        "domain": domain,
        "speech": speech,
        "count_active": count_active,
        "count_inactive": count_inactive,
        "count_unavailable": count_unavailable,
        "count_on": count_active,
        "count_off": count_inactive,
        "total_authorized": total,
        "active_names": active_names,
        "mode": "ok",
    }
