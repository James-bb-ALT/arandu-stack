"""Deterministic read-only aggregated light query handler (no LLM needed)."""
from __future__ import annotations

import re
import unicodedata
from typing import Any


def _norm(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text or "")
    plain = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", plain.lower().strip())


_LIGHT_QUERY_RE = re.compile(
    r"\b(?:quantas?\s+luz(?:es)?|quais\s+luz(?:es)?|tem\s+alguma\s+luz|alguma\s+luz\s+(?:acesa|ligada))\b"
)


def is_aggregated_light_query(text: str) -> bool:
    norm = _norm(text)
    if not _LIGHT_QUERY_RE.search(norm):
        return False
    # Never hijack lock/camera/other domain queries
    if any(tok in norm for tok in ("camera", "fechadura", "porta", "portao", "alarme")):
        return False
    return True


def _extract_area_filter(norm_text: str, catalog: dict[str, Any]) -> tuple[str | None, str | None, bool]:
    """Return (area_id, area_name, area_explicit_not_found)."""
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
                    best = (len(n_name), area_id, str(area.get("names", [area_id])[0] if area.get("names") else area_id))
    if best:
        return best[1], best[2], False
    # Check if user explicitly named an unknown area like "na garagem"
    m = re.search(r"\b(?:na|no)\s+([a-z0-9_-]{3,})\b", norm_text)
    if m and m.group(1) not in ("casa", "residencia"):
        return None, m.group(1), True
    return None, None, False


def evaluate_aggregated_light_query(
    text: str,
    snapshot: Any,
    hass: Any | None = None,
    *,
    states_map: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Evaluate an authorized, deduplicated read-only light aggregation query.

    Returns dict with {speech, count_on, count_off, count_unavailable, total_authorized, mode}
    or None if text is not an aggregated light query.
    """
    if not is_aggregated_light_query(text):
        return None
    norm = _norm(text)
    payload = getattr(snapshot, "payload", None) or {}
    by_id = getattr(snapshot, "by_id", None)
    entities = list(by_id.values()) if isinstance(by_id, dict) else list(payload.get("entities") or [])

    area_id, area_label, missing_area = _extract_area_filter(norm, payload)
    if missing_area:
        return {
            "speech": f"Não encontrei luzes autorizadas na área {area_label}.",
            "count_on": 0,
            "count_off": 0,
            "count_unavailable": 0,
            "total_authorized": 0,
            "mode": "area_not_found",
        }

    # Deduplicate authorized light entities by entity_id
    seen: dict[str, dict[str, Any]] = {}
    for row in entities:
        if row.get("domain") != "light":
            continue
        if area_id is not None and row.get("area_id") != area_id:
            continue
        eid = row.get("entity_id")
        if isinstance(eid, str) and eid not in seen:
            seen[eid] = row

    if not seen:
        return {
            "speech": "Não há luzes autorizadas disponíveis para essa consulta.",
            "count_on": 0,
            "count_off": 0,
            "count_unavailable": 0,
            "total_authorized": 0,
            "mode": "no_authorized_lights",
        }

    on_names: list[str] = []
    count_on = 0
    count_off = 0
    count_unavailable = 0

    for eid, row in seen.items():
        state_val: str | None = None
        if states_map is not None and eid in states_map:
            state_val = states_map[eid]
        elif hass is not None and getattr(hass, "states", None) is not None:
            st_obj = hass.states.get(eid)
            if st_obj is not None:
                state_val = getattr(st_obj, "state", None)
        if state_val is None:
            attr_state = (row.get("attributes") or {}).get("state")
            if isinstance(attr_state, str):
                state_val = attr_state
            elif (row.get("attributes") or {}).get("available") is False:
                state_val = "unavailable"

        norm_state = str(state_val or "unknown").lower()
        if norm_state == "on":
            count_on += 1
            on_names.append(row.get("name") or eid)
        elif norm_state == "off":
            count_off += 1
        else:
            count_unavailable += 1

    scope_suffix = f" em {area_label}" if area_label else ""
    unavail_note = (
        f" ({count_unavailable} indisponível para leitura)"
        if count_unavailable == 1
        else f" ({count_unavailable} indisponíveis para leitura)"
        if count_unavailable > 1
        else ""
    )

    if count_on == 0 and count_off == 0 and count_unavailable > 0:
        return {
            "speech": f"Não tenho acesso a estados de luzes para essa consulta{scope_suffix}{unavail_note}.",
            "count_on": 0,
            "count_off": 0,
            "count_unavailable": count_unavailable,
            "total_authorized": len(seen),
            "mode": "all_unavailable",
        }

    if "desligad" in norm or "apagad" in norm:
        noun = "luz desligada" if count_off == 1 else "luzes desligadas"
        verb = "Há"
        speech = f"{verb} {count_off} {noun}{scope_suffix}{unavail_note}."
        mode = "count_off"
    elif "quais" in norm:
        if count_on == 0:
            speech = f"Nenhuma luz está ligada{scope_suffix}{unavail_note}."
        else:
            speech = f"Estão ligadas{scope_suffix}: {', '.join(on_names)}{unavail_note}."
        mode = "list_on"
    elif "alguma" in norm:
        if count_on > 0:
            noun = "luz ligada" if count_on == 1 else "luzes ligadas"
            speech = f"Sim, há {count_on} {noun}{scope_suffix}{unavail_note}."
        else:
            speech = f"Não há luzes ligadas no momento{scope_suffix}{unavail_note}."
        mode = "any_on"
    else:
        noun = "luz ligada" if count_on == 1 else "luzes ligadas"
        speech = f"Há {count_on} {noun}{scope_suffix}{unavail_note}."
        mode = "count_on"

    return {
        "speech": speech,
        "count_on": count_on,
        "count_off": count_off,
        "count_unavailable": count_unavailable,
        "total_authorized": len(seen),
        "mode": mode,
    }
