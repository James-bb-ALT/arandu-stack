from __future__ import annotations

from collections import defaultdict
import math
import random

from .common import canonical

SPLITS = ("train", "validation", "test")


def split_records(records: list[dict], ratios=(0.8, 0.1, 0.1), seed=42, mode="phrase") -> dict[str, list[dict]]:
    if len(ratios) != 3 or any(not math.isfinite(x) or x < 0 for x in ratios) or not math.isclose(sum(ratios), 1.0):
        raise ValueError("As três proporções devem ser finitas, não negativas e somar 1.")
    if mode not in ("phrase", "speaker"):
        raise ValueError("split mode inválido")
    parent = list(range(len(records)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a, b):
        parent[find(b)] = find(a)

    seen = {}
    for i, record in enumerate(records):
        keys = [("phrase", canonical(record["text"])), ("pcm", record["pcm_sha256"])]
        if mode == "speaker":
            keys.append(("speaker", record["speaker"]))
        for key in keys:
            if key in seen:
                union(i, seen[key])
            else:
                seen[key] = i
    groups = defaultdict(list)
    for i, record in enumerate(records):
        groups[find(i)].append(record)
    chunks = list(groups.values())
    required = [s for s, ratio in zip(SPLITS, ratios) if ratio > 0]
    if len(chunks) < len(required):
        raise ValueError(f"Somente {len(chunks)} grupos independentes para {len(required)} conjuntos. "
                         "Adicione frases; em modo speaker, roteiros compartilhados conectam locutores e impedem isolamento.")
    rng = random.Random(seed)
    chunks.sort(key=lambda g: min(r["id"] for r in g))
    rng.shuffle(chunks)
    chunks.sort(key=len, reverse=True)
    result = {s: [] for s in SPLITS}
    targets = dict(zip(SPLITS, [ratio * len(records) for ratio in ratios]))
    for index, group in enumerate(chunks):
        empty = [s for s in required if not result[s]]
        candidates = empty if len(chunks) - index == len(empty) else required
        chosen = max(candidates, key=lambda s: targets[s] - len(result[s]))
        result[chosen].extend(group)
    for rows in result.values():
        rows.sort(key=lambda r: r["id"])
    return result
