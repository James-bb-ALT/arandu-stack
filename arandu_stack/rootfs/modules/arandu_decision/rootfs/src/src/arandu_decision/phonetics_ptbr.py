"""PT-BR phonetic/lexical normalization and cheap intent triage (no HA execution)."""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable

ACTION_LEXICON: dict[str, tuple[str, ...]] = {
    "turn_on": ("liga", "ligar", "acende", "acender", "ativa", "ativar", "inicia", "coloca para funcionar"),
    "turn_off": ("desliga", "desligar", "apaga", "apagar", "desativa", "desativar", "encerra"),
    "query": ("consulta", "consultar", "qual", "como esta", "estado", "status", "verifica", "mostra", "lista"),
    "open": ("abre", "abrir", "ergue", "sobe", "levantar"),
    "close": ("fecha", "fechar", "abaixa", "desce", "baixar"),
    "play": ("toca", "tocar", "reproduz", "reproduzir", "continua", "retoma", "volta a tocar"),
    "pause": ("pausa", "pausar", "da um tempo", "suspende"),
    "stop": ("para", "parar", "interrompe", "interromper"),
    "next": ("proxima", "proximo", "pula", "avanca"),
    "previous": ("anterior", "volta musica"),
    "volume": ("volume", "aumenta o som", "abaixa o som", "som"),
    "mute": ("muta", "silencia", "tira o som"),
    "unmute": ("desmuta", "volta o som"),
    "brightness": ("brilho", "intensidade", "clareia", "escurece", "diminui a luz", "aumenta a luz"),
    "percentage": ("por cento", "porcento", "velocidade", "nivel"),
    "temperature": ("temperatura", "graus", "esquenta", "esfria"),
    "vacuum_start": ("aspira", "aspirar", "limpa", "limpar", "inicia limpeza", "comeca limpeza"),
    "vacuum_pause": ("pausa limpeza", "pausa o aspirador"),
    "vacuum_stop": ("para limpeza", "para o aspirador"),
    "vacuum_dock": ("volta para base", "recolhe aspirador", "carrega aspirador"),
    "lock": ("tranca", "trancar", "fecha fechadura"),
    "unlock": ("destranca", "destrancar", "abre fechadura"),
    "activate": ("ativa cena", "modo", "executa cena"),
    "timer_start": ("cria timer", "faz timer", "inicia timer", "define timer", "temporizador"),
    "timer_cancel": ("cancela timer", "apaga timer"),
    "clock_alarm_create": ("despertador", "alarme", "me acorda"),
    "reminder_create": ("lembrete", "me lembra", "lembrar"),
    "todo_add": ("adiciona na lista", "coloca na lista", "anota"),
    "todo_list": ("lista de compras", "o que tem na lista"),
    "history_query": ("historico", "ontem", "consumo", "quanto gastou"),
}

_CONVERSATION_CUES = (
    "conta uma piada",
    "me conta uma piada",
    "curiosidade",
    "quem foi",
    "o que significa",
    "como funciona",
    "bom dia",
    "boa tarde",
    "boa noite",
    "tudo bem",
    "obrigado",
    "valeu",
    "poema",
    "historia",
    "receita",
    "explica",
)

_QUERY_CUES = (
    "qual a ",
    "qual o ",
    "quais ",
    "quanto ",
    "quantos ",
    "esta ligado",
    "esta aberta",
    "esta fechada",
    "temperatura",
    "umidade",
    "consumo",
)


def normalize_ptbr(text: str) -> str:
    nfd = unicodedata.normalize("NFD", text.casefold())
    cleaned = "".join(ch if (ch.isalnum() or ch.isspace()) else " " for ch in nfd if not unicodedata.combining(ch))
    return " ".join(cleaned.split())


def phonetic_ptbr(word: str) -> str:
    w = normalize_ptbr(word).replace(" ", "")
    if not w:
        return ""
    replacements = (
        ("qu", "k"),
        ("gu", "g"),
        ("ch", "x"),
        ("sh", "x"),
        ("lh", "l"),
        ("nh", "n"),
        ("ph", "f"),
        ("ss", "s"),
        ("sc", "s"),
        ("xc", "s"),
        ("ce", "se"),
        ("ci", "si"),
        ("ge", "je"),
        ("gi", "ji"),
        ("ng", "g"),  # catches ASR nasal epenthesis like "lingua" -> "liga"
        ("z", "s"),
        ("w", "v"),
        ("y", "i"),
    )
    for src, dst in replacements:
        w = w.replace(src, dst)
    # Collapse consecutive duplicate characters
    out: list[str] = []
    for ch in w:
        if not out or out[-1] != ch:
            out.append(ch)
    res = "".join(out)
    if len(res) > 3 and res.endswith(("r", "s")):
        res = res[:-1]
    return res


def levenshtein_similarity(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    la, lb = len(a), len(b)
    prev = list(range(lb + 1))
    for i, ca in enumerate(a, start=1):
        curr = [i]
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            curr.append(min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost))
        prev = curr
    dist = prev[-1]
    return max(0.0, 1.0 - dist / max(la, lb))


def lexical_phonetic_score(token: str, candidate_phrase: str) -> float:
    nt = normalize_ptbr(token)
    nc = normalize_ptbr(candidate_phrase)
    if not nt or not nc:
        return 0.0
    if nt == nc:
        return 1.0
    pt, pc = phonetic_ptbr(nt), phonetic_ptbr(nc)
    if pt == pc and len(pt) >= 3:
        return 0.92
    raw_sim = levenshtein_similarity(nt, nc)
    phon_sim = levenshtein_similarity(pt, pc)
    return max(raw_sim, phon_sim * 0.96)


def rank_action_candidates(
    clause_text: str,
    allowed_actions: Iterable[str],
    *,
    encoder=None,
) -> list[dict]:
    """Rank ONLY allowed_actions for an already-constrained target."""
    allowed = sorted(set(allowed_actions))
    if not allowed:
        return []
    tokens = [t for t in normalize_ptbr(clause_text).split() if len(t) >= 2]
    results: list[dict] = []
    for action in allowed:
        forms = ACTION_LEXICON.get(action, (action.replace("_", " "),))
        best_lex = 0.0
        matched_form = None
        for form in forms:
            if " " in form:
                if form in normalize_ptbr(clause_text):
                    best_lex = max(best_lex, 0.95)
                    matched_form = form
                continue
            for tok in tokens:
                score = lexical_phonetic_score(tok, form)
                if score > best_lex:
                    best_lex = score
                    matched_form = f"{tok}~{form}"
        evidence = []
        if best_lex >= 0.72:
            evidence.append(f"phonetic_ptbr:{matched_form or action}")
        results.append(
            {
                "value": action,
                "score": round(best_lex, 4),
                "lexical_score": round(best_lex, 4),
                "semantic_score": None,
                "evidence": evidence,
            }
        )

    if encoder is not None and getattr(encoder, "model", None) is not None:
        try:
            q_vec = encoder.encode([clause_text], query=True)[0]
            passages = [" ".join(ACTION_LEXICON.get(r["value"], (r["value"],))) for r in results]
            p_vecs = encoder.encode(passages, query=False)
            for row, p_vec in zip(results, p_vecs):
                sim = float((q_vec * p_vec).sum())
                row["semantic_score"] = round(sim, 4)
                row["score"] = round(max(row["lexical_score"], 0.65 * row["lexical_score"] + 0.35 * max(0.0, sim)), 4)
                if sim >= 0.55:
                    row["evidence"].append("semantic_embedding")
        except Exception:
            pass

    results.sort(key=lambda r: (-r["score"], r["value"]))
    return results


def cheap_triage(text: str, partial: dict | None = None) -> str:
    """Zero-weight deterministic triage: possible_action | ha_query | general_conversation | denied | unknown."""
    if partial:
        if partial.get("safety_flags") or partial.get("request_kind") == "safety_refusal":
            return "denied"
    norm = normalize_ptbr(text)
    if any(cue in norm for cue in _CONVERSATION_CUES):
        return "general_conversation"
    if partial:
        for clause in partial.get("clauses") or ():
            c = clause.get("constraints") or {}
            if c.get("negated") or c.get("conditional") or c.get("quoted_content"):
                return "denied"
            if clause.get("slots", {}).get("target", {}).get("status") in {"resolved", "conflicting", "candidate"}:
                return "possible_action"
    if any(norm.startswith(cue) or f" {cue}" in norm for cue in _QUERY_CUES):
        return "ha_query"
    return "unknown"
