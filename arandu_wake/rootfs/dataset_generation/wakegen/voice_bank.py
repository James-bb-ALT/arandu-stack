"""Perfis sintéticos sem nomes de pessoas; split fixado antes da síntese."""
import hashlib
import itertools
import random

import numpy as np

from .common import stable_seed

REGIONS = [
    ("Nordeste", "pernambucano"), ("Nordeste", "baiano"), ("Nordeste", "cearense"),
    ("Norte", "paraense"), ("Norte", "amazonense"),
    ("Sudeste", "mineiro"), ("Sudeste", "paulistano"), ("Sudeste", "carioca"),
    ("Sul", "gaúcho"), ("Sul", "paranaense"),
    ("Centro-Oeste", "goiano"), ("Centro-Oeste", "mato-grossense"),
    ("neutro", "brasileiro neutro"),
]


def profiles(config):
    count = config["voice_bank"]["profiles"]
    combinations = list(itertools.product(
        ["feminina", "masculina", "andrógina"],
        ["jovem adulta", "adulta de meia-idade", "idosa"],
        ["grave", "médio", "agudo"],
        ["limpo", "levemente rouco", "aveludado", "levemente nasal"],
        ["pausada", "cotidiana", "rápida mas clara"],
        range(len(REGIONS)),
    ))
    # Balancear os grupos principais sem depender apenas de um sorteio de seeds.
    rng = random.Random(config["seed"])
    rng.shuffle(combinations)
    selected = []
    seen = set()
    for index in range(count):
        wanted_gender = ["feminina", "masculina", "andrógina"][index % 3]
        wanted_region = index % len(REGIONS)
        match = next((v for v in combinations if v not in seen and v[0] == wanted_gender and v[-1] == wanted_region), None)
        if match is None:
            raise ValueError("Número de perfis excede combinações de descrições distintas")
        seen.add(match)
        selected.append(match)
    train = int(count * config["voice_bank"]["splits"]["train"])
    validation = int(count * config["voice_bank"]["splits"]["validation"])
    for index, (gender, age, pitch, texture, pace, region_index) in enumerate(selected):
        region, accent = REGIONS[region_index]
        split = "train" if index < train else "validation" if index < train + validation else "test"
        description = ("Speak exclusively in native BRAZILIAN Portuguese (pt-BR), with Brazilian pronunciation, "
                       "Brazilian vowel timing and conversational Brazilian intonation. Never use European "
                       "Portuguese or a Portugal accent. Keep unstressed vowels naturally audible as in Brazil; "
                       "do not use the strongly reduced vowels of European Portuguese. "
                       f"Voz sintética {gender}, pessoa {age}, português do Brasil, timbre {pitch} e {texture}, "
                       f"articulação {pace}, sotaque {accent} discreto e natural. Sem caricatura regional. "
                       "Apenas uma pessoa, voz limpa, sem música. A palavra Arandu tem três sílabas A-RAN-DU, "
                       "com final DU /du/ e acento natural na sílaba final, nunca arando. Não leia estas instruções.")
        yield {"speaker_id": f"spk_{index:04d}", "index": index, "split": split,
               "locale": "pt-BR", "accent_policy_version": config["voice_bank"]["accent_policy_version"],
               "accent_validation": "pending_listening_review",
               "gender_description": gender, "age_description": age, "pitch_description": pitch,
               "texture": texture, "pace": pace, "region": region, "accent": accent,
               "description": description, "reference_seed": stable_seed(config["seed"], "reference", index),
               "description_sha256": hashlib.sha256(description.encode()).hexdigest(),
               "identity_status": "planned_not_acoustically_verified"}


def nearest_similarity(embedding, existing):
    vector = np.asarray(embedding, dtype=float).reshape(-1)
    vector /= max(np.linalg.norm(vector), 1e-12)
    best, nearest = -1.0, None
    for speaker_id, other in existing:
        other = np.asarray(other, dtype=float).reshape(-1)
        score = float(vector @ other / max(np.linalg.norm(other), 1e-12))
        if score > best:
            best, nearest = score, speaker_id
    return {"cosine_similarity": best if nearest else None, "nearest_speaker_id": nearest}


def assess_diversity(candidate, existing, warning_threshold):
    """Cosine não calibrado indica revisão, não probabilidade de mesma identidade."""
    nearest = nearest_similarity(candidate["speaker_embedding"],
                                 [(v["speaker_id"], v["speaker_embedding"]) for v in existing])
    exact_audio = any(v["reference_sha256"] == candidate["reference_sha256"] for v in existing)
    exact_embedding = any(np.array_equal(candidate["speaker_embedding"], v["speaker_embedding"]) for v in existing)
    score = nearest["cosine_similarity"]
    return {"policy": "uncalibrated_cosine_review_v2", "similarity": nearest,
            "rejection_reason": "duplicate_reference_audio" if exact_audio else "identical_speaker_embedding" if exact_embedding else None,
            "requires_review": score is not None and score >= warning_threshold,
            "identity_distinctness": "not_verified", "threshold_interpretation": "review_only_not_identity_classifier"}
