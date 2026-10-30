"""Deterministic semantic clarification over an already authorized catalog.

Kept identical in the standalone LLM package and HA integration.
This module never grants execution permission or invents device/area names.
"""
from __future__ import annotations

import re
import unicodedata


def normalized(value):
    return " ".join(re.findall(r"\w+", "".join(c for c in unicodedata.normalize("NFD", value.casefold()) if not unicodedata.combining(c))))


STOP = {"a", "o", "as", "os", "de", "da", "do", "das", "dos", "na", "no", "em", "e", "um", "uma"}
ACTIONS = {
    "turn_on": {"liga", "ligar", "ligue", "ligue", "acende", "acenda", "acender", "ativa", "ative", "ativar"},
    "turn_off": {"desliga", "desligue", "desligar", "apaga", "apague", "apagar", "desativa", "desative", "desativar"},
    "query": {"qual", "quais", "quanto", "quantos", "consulta", "consultar", "consulte", "estado"},
}


def understood_action(text):
    words = set(normalized(text).split())
    if words & {"nao", "nunca", "exceto", "menos", "se", "quando"}:
        return None
    matches = [action for action, verbs in ACTIONS.items() if words & verbs]
    return matches[0] if len(matches) == 1 else None


def resolve_device(text, catalog, context=None, allowed=None):
    """Explicit area wins; otherwise use registered origin, never a model area."""
    query = normalized(text)
    words = set(query.split()) - STOP
    areas = {a["area_id"]: a for a in catalog["areas"]}
    mentioned = {key for key, area in areas.items() if any(
        normalized(name) and (" " + normalized(name) + " ") in (" " + query + " ")
        for name in area["names"])}
    if len(mentioned) > 1:
        return None
    origin = (context or {}).get("origin_area")
    area = next(iter(mentioned)) if mentioned else origin if origin in areas else None
    # An explicit spatial phrase outside known areas must not silently use origin.
    if not mentioned and re.search(r"\b(?:no|na|nos|nas|em|do|da|dos|das)\s+", text.casefold()):
        return None
    scores = {}
    for row in catalog["entities"]:
        if allowed is not None and row["entity_id"] not in allowed:
            continue
        if row.get("attributes", {}).get("available") is not True:
            continue
        if area is not None and row.get("area_id") != area:
            continue
        room_words = set(normalized(" ".join(areas.get(row.get("area_id"), {}).get("names", []))).split())
        names = set(normalized(" ".join([row["name"], *row.get("aliases", [])])).split())
        evidence = words & (names - room_words - STOP)
        if evidence:
            scores[row["entity_id"]] = (len(evidence), row)
    if not scores:
        return None
    best = max(score for score, _ in scores.values())
    winners = [row for score, row in scores.values() if score == best]
    return winners[0] if len(winners) == 1 else None


def clarification(text, catalog, context=None, allowed=None, reason="not_understood"):
    row = resolve_device(text, catalog, context, allowed)
    question = "Não entendi o que você deseja. Pode repetir dizendo o dispositivo e a ação?"
    if row:
        label = row["name"]
        area = next((a for a in catalog["areas"] if a["area_id"] == row.get("area_id")), None)
        if area and area["names"] and normalized(area["names"][0]) not in normalized(label):
            label += " (" + area["names"][0] + ")"
        if understood_action(text) is None:
            question = f"Acredito que você esteja falando de {label}, mas não entendi o que fazer. Pode repetir?"
        else:
            question = f"Acredito que você esteja falando de {label}, mas não consegui validar esse pedido. Pode repetir?"
    return {"intent": None, "target_entities": [row["entity_id"]] if row else [],
            "target_area": row.get("area_id") if row else None, "confidence": None,
            "requires_clarification": True, "clarification_question": question, "reason": reason}
