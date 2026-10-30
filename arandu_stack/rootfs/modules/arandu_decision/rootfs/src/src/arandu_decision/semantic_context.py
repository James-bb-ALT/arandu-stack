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


def device_candidates(text, catalog, context=None, allowed=None, action=None):
    """Explicit area wins; otherwise use registered origin, never a model area."""
    query = normalized(text)
    words = set(query.split()) - STOP
    areas = {a["area_id"]: a for a in catalog["areas"]}
    mentioned = {key for key, area in areas.items() if any(
        normalized(name) and (" " + normalized(name) + " ") in (" " + query + " ")
        for name in area["names"])}
    if len(mentioned) > 1:
        return []
    origin = (context or {}).get("origin_area")
    area = next(iter(mentioned)) if mentioned else origin if origin in areas else None
    # An explicit spatial phrase outside known areas must not silently use origin.
    if not mentioned:
        qualifiers = re.findall(r"\b(?:no|na|nos|nas|em|do|da|dos|das)\s+\w+", query)
        if qualifiers:
            labels = [normalized(label) for item in catalog["entities"]
                      for label in [item["name"], *item.get("aliases", [])]
                      if normalized(label) and (" " + normalized(label) + " ") in (" " + query + " ")]
            if any(not any((" " + qualifier + " ") in (" " + label + " ") for label in labels) for qualifier in qualifiers):
                return []
    scores = {}
    for row in catalog["entities"]:
        if allowed is not None and row["entity_id"] not in allowed:
            continue
        if row.get("attributes", {}).get("available") is not True:
            continue
        if action is not None and action not in row["actions"]:
            continue
        if area is not None and row.get("area_id") != area:
            continue
        room_words = set(normalized(" ".join(areas.get(row.get("area_id"), {}).get("names", []))).split())
        names = set(normalized(" ".join([row["name"], *row.get("aliases", [])])).split())
        evidence = words & (names - room_words - STOP)
        if evidence:
            heads = [next((word for word in normalized(label).split() if word not in STOP and word not in room_words), None)
                     for label in [row["name"], *row.get("aliases", [])]]
            # A device noun matches the device itself before an incidental mention
            # in "velocidade do ventilador". Two genuine fans remain tied.
            score = (len(evidence), int(any(head in words for head in heads if head)))
            scores[row["entity_id"]] = (score, row)
    if not scores:
        return []
    best = max(score for score, _ in scores.values())
    winners = [row for score, row in scores.values() if score == best]
    return sorted(winners, key=lambda row: row["entity_id"])


def resolve_device(text, catalog, context=None, allowed=None, action=None):
    rows = device_candidates(text, catalog, context, allowed, action)
    return rows[0] if len(rows) == 1 else None


def clarification(text, catalog, context=None, allowed=None, reason="not_understood"):
    row = resolve_device(text, catalog, context, allowed)
    question = "Não entendi o que você deseja. Pode repetir dizendo o dispositivo e a ação?"
    matches = device_candidates(text, catalog, context, allowed)
    if len(matches) > 1:
        labels = []
        for item in matches[:4]:
            area = next((a for a in catalog["areas"] if a["area_id"] == item.get("area_id")), None)
            label = item["name"]
            if area and area["names"] and normalized(area["names"][0]) not in normalized(label):
                label += " (" + area["names"][0] + ")"
            labels.append(label)
        question = "Encontrei mais de um dispositivo: " + "; ".join(labels) + ". De qual você está falando?"
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

def hypothesis_allowed(text):
    words = set(normalized(text).split())
    return (not words & {"nao", "nunca", "exceto", "menos", "se", "quando"}
            and sum(bool(words & verbs) for verbs in ACTIONS.values()) <= 1)


def decomposition(text, catalog, context=None):
    """Three diagnostic slots; spelling/embedding similarity never authorizes."""
    from difflib import SequenceMatcher
    row = resolve_device(text, catalog, context)
    areas = {a["area_id"]: a for a in catalog["areas"]}
    query = normalized(text)
    explicit = [key for key, area in areas.items() if any(
        normalized(name) and (" " + normalized(name) + " ") in (" " + query + " ")
        for name in area["names"])]
    origin = (context or {}).get("origin_area")
    area_id = explicit[0] if len(explicit) == 1 else origin if not explicit and origin in areas else None
    excluded = set(STOP)
    if row:
        excluded.update(normalized(" ".join([row["name"], *row.get("aliases", [])])).split())
    if area_id:
        excluded.update(normalized(" ".join(areas[area_id]["names"])).split())
    matches = device_candidates(text, catalog, context)
    verbs = set().union(*ACTIONS.values())
    for candidate in matches:
        excluded.update(set(normalized(" ".join([candidate["name"], *candidate.get("aliases", [])])).split()) - verbs)
    action_words = [word for word in query.split() if word not in excluded]
    ranking = [{"intent": action, "lexical_similarity": max(
        (SequenceMatcher(None, word, verb).ratio() for word in action_words for verb in verbs),
        default=0.0)} for action, verbs in ACTIONS.items()]
    ranking.sort(key=lambda item: (-item["lexical_similarity"], item["intent"]))
    return {"action": {"text": " ".join(action_words), "exact": understood_action(text), "ranking": ranking},
            "device": {"entity_id": row["entity_id"] if row else None, "name": row["name"] if row else None, "unambiguous": row is not None,
                       "candidates": [{"entity_id": item["entity_id"], "name": item["name"], "area_id": item.get("area_id")} for item in matches[:8]]},
            "area": {"area_id": area_id, "source": "explicit" if explicit else "origin" if area_id else "unknown"},
            "note": "Similaridades são hipóteses, não probabilidades nem autorização."}


def confirmation_question(action, row, catalog):
    label = row["name"]
    area = next((a for a in catalog["areas"] if a["area_id"] == row.get("area_id")), None)
    if area and area["names"] and normalized(area["names"][0]) not in normalized(label):
        label += " (" + area["names"][0] + ")"
    verb = {"turn_on": "ligar", "turn_off": "desligar", "query": "consultar o estado de"}[action]
    return f"Acho que você gostaria de {verb} {label}. É isso?"
