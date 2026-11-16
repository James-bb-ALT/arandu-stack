"""Typed, privacy-safe outcomes shared by runtime and the HA speech boundary."""

import re

from .contextual_protocol import normalize

MESSAGES = {
    'history_unavailable':'O histórico não está disponível nesta configuração ou versão do Home Assistant.',
    'history_timeout':'A consulta ao histórico excedeu o tempo permitido.',
    'history_busy':'Já há uma consulta ao histórico em andamento. Tente novamente em instantes.',
    'insufficient_history':'Não há dados suficientes e contínuos para responder sobre esse período.',
    'power_or_energy':'Você quer comparar potência instantânea em W ou energia consumida em kWh?',
    'todo_item_not_unique':'Não encontrei um único item com esse nome nessa lista. Especifique o item e a lista.',
    'todo_completed_set_changed':'Os itens concluídos mudaram após a confirmação. Faça o pedido novamente.',
    'undo_state_changed':'O estado mudou depois da última operação. Não vou sobrescrever essa mudança.',
    'undo_unavailable':'Não há uma mudança reversível válida nesta conversa.',
    "missing_user": "Este dispositivo de voz ainda não está associado a um usuário.",
    "inactive_or_missing_configured_user": "O usuário associado a este dispositivo de voz está indisponível. Confira a configuração do ARANDU NLU.",
    "identity_origin_unavailable": "O dispositivo de voz associado está indisponível. Confira a configuração do ARANDU NLU.",
    "identity_changed": "A identidade da conversa mudou. Faça o pedido novamente.",
    "unknown_area": "Não encontrei um cômodo com esse nome.",
    "ambiguous_area": "Há mais de um cômodo com esse nome. Em qual cômodo?",
    "no_accessible_sensor": "Não encontrei um sensor acessível para essa medição nesse cômodo ou aparelho.",
    "sensor_unavailable": "O sensor dessa medição está indisponível no momento.",
    "ambiguous_source": "Qual sensor você quer consultar?",
    "invalid_measurement": "O sensor não forneceu uma medição válida.",
    "measurement_stale": "Não há uma observação recente dessa medição.",
    "incompatible_units": "As unidades dos sensores são incompatíveis com essa medição.",
    "no_compatible_capability": "Não encontrei dispositivos acessíveis com essa capacidade.",
    "target_not_found": "Não encontrei um dispositivo com esse nome.",
    "permission_denied": "Você não tem permissão para fazer isso.",
    "invalid_request": "O pedido contém dados inválidos ou excede os limites permitidos.",
    "unsupported_intent": "Essa operação ainda não é suportada.",
    "no_match": "Não entendi esse pedido. Diga a ação e o cômodo ou aparelho.",
    "backend_unavailable": "Não consegui acessar o serviço local do ARANDU NLU.",
    "stale": "Os dispositivos ou o contexto mudaram. Faça o pedido novamente.",
    "target_limit": "Esse conjunto excede o limite de 32 dispositivos. Nenhum foi acionado; peça um conjunto menor.",
    "bulk_target_unavailable": "Há um dispositivo indisponível nesse conjunto. Nenhum foi acionado.",
    "bulk_policy": "Esse conjunto não é autorizado pela política de ações coletivas.",
    "implicit_origin_policy": "Não consegui validar o cômodo de origem desse comando.",
    "bulk_target_set_changed": "O conjunto de dispositivos mudou. Nenhum foi acionado; faça o pedido novamente.",
    "dialogue_changed": "O contexto, as permissões ou os dispositivos mudaram. Faça o pedido novamente.",
    "dialogue_expired": "A pergunta anterior expirou. Faça o pedido novamente.",
    "no_location_provider": "Não há uma fonte de localização acessível configurada para esse aparelho.",
    "location_stale": "Não há uma observação recente da localização desse aparelho.",
    "location_unknown_area": "A fonte de localização não identificou um cômodo cadastrado.",
    "location_ambiguous": "Há mais de uma localização possível para esse aparelho.",
    "device_not_identified": "Não identifiquei esse aparelho no catálogo acessível.",
    "semantic_negation": "Entendi um pedido para não executar essa ação. Nenhuma alteração foi feita.",
    "semantic_condition": "Não consigo agendar ou aguardar condições externas para executar esse comando.",
    "semantic_exclusion": "Não consegui identificar com segurança qual dispositivo excluir desse comando coletivo.",
    "semantic_quoted_command": "Entendi o comando apenas como uma citação ou pergunta, por isso não executei nenhuma ação.",
    "semantic_non_effect_request": "Esse pedido parece ser uma consulta ou comentário, não um comando de acionamento.",
    "semantic_safety_refusal": "Não executei: o pedido contém uma restrição que não consigo aplicar com segurança.",
    "missing_origin_area": "Entendi o comando, mas não identifiquei o cômodo. Em qual cômodo?",
    "max_clarification_attempts": "Ainda não consegui identificar o comando completo. Diga novamente informando a ação, o aparelho e o cômodo.",
}


def speech(code: str, reason: str | None) -> str | None:
    aliases = {"entity_unavailable": "sensor_unavailable", "entity_permission": "permission_denied", "not_exposed": "permission_denied"}
    return MESSAGES.get(aliases.get(reason, reason), MESSAGES.get(code))


_ACTION_VERBS = {
    "turn_on": "ligar",
    "turn_off": "desligar",
    "open": "abrir",
    "close": "fechar",
    "lock": "trancar",
    "unlock": "destrancar",
    "activate": "ativar",
    "play": "tocar",
    "pause": "pausar",
    "stop": "parar",
    "brightness": "ajustar",
    "temperature": "ajustar",
    "percentage": "ajustar",
    "query": "consultar",
}


def action_verb(action: str | None) -> str | None:
    return _ACTION_VERBS.get(action or "")


def spoken_reason_category(code: str | None, reason: str | None) -> str:
    if code in ("success", "query_success", "executed"):
        return "execution"
    if code == "confirmation_required":
        return "confirmation"
    if reason and reason.startswith("semantic_") and reason not in ("semantic_clarification", "semantic_confirmation"):
        return "restriction"
    if code in ("denied",) or reason in ("permission_denied", "entity_permission", "not_exposed", "authentication_required", "bulk_policy", "sensitive_policy"):
        return "restriction"
    if code in ("missing_slot", "clarification") or reason in (
        "missing_origin_area", "unknown_area", "ambiguous_area", "ambiguous_target",
        "ambiguous_source", "target_not_found", "no_compatible_capability",
        "missing_target", "missing_media_query", "action_unresolved", "target_unresolved",
        "area_unresolved", "capability_unsupported", "max_clarification_attempts",
        "bulk_clarification", "semantic_clarification", "llm_abstention",
        "unresolved_progressive_pipeline",
    ):
        return "clarification"
    if code in ("unavailable",) or reason in ("backend_unavailable", "queue_saturated", "request_timeout", "ha_execution_failed"):
        return "error"
    if code == "cancelled":
        return "cancelled"
    return "no_match"


def render_contextual_message(
    reason_code: str | None,
    *,
    code: str | None = None,
    mention: str | None = None,
    action: str | None = None,
    area_name: str | None = None,
    candidate_names: list[str] | tuple[str, ...] = (),
    target_names: list[str] | tuple[str, ...] = (),
    domain: str | None = None,
    stage: str | None = None,
    metric: str | None = None,
) -> str:
    """Centralized typed rendering for diagnostic errors, restrictions, and slot clarifications."""
    del stage
    verb = action_verb(action)
    clean_mention = mention.strip() if isinstance(mention, str) and mention.strip() else None
    names = [str(item).strip() for item in candidate_names if isinstance(item, str) and str(item).strip()]
    targets = [str(item).strip() for item in target_names if isinstance(item, str) and str(item).strip()]

    if reason_code in (
        "semantic_negation",
        "semantic_condition",
        "semantic_exclusion",
        "semantic_quoted_command",
        "semantic_non_effect_request",
        "semantic_safety_refusal",
        "max_clarification_attempts",
    ):
        return MESSAGES[reason_code]

    if reason_code in ("missing_origin_area", "area_unresolved", "unknown_area", "ambiguous_area"):
        if reason_code == "unknown_area":
            return f"Não encontrei o cômodo '{area_name}'. Em qual cômodo?" if area_name else "Não encontrei um cômodo com esse nome. Em qual cômodo?"
        if reason_code == "ambiguous_area":
            return "Há mais de um cômodo com esse nome. Em qual cômodo?"
        if domain == "light" and verb:
            return f"Entendi que você quer {verb} as luzes. Em qual cômodo?"
        if verb and clean_mention:
            return f"Entendi que você quer {verb} {clean_mention}, mas não identifiquei o cômodo. Em qual cômodo?"
        if verb:
            return f"Entendi que você quer {verb}, mas não sei em qual cômodo. Em qual cômodo?"
        return MESSAGES["missing_origin_area"]

    if reason_code in ("ambiguous_target", "target_ambiguous", "ambiguous_source"):
        if names:
            listing = f"{names[0]} ou {names[1]}" if len(names) == 2 else ", ".join(names[:4])
            if reason_code == "ambiguous_source":
                kind = "aparelho" if metric in ("battery", "location") else "sensor"
                return f"Encontrei {listing}. Qual {kind} você quer consultar?"
            if area_name and verb:
                return f"Encontrei mais de um aparelho em {area_name} ({listing}). Qual deles você quer {verb}?"
            if verb:
                return f"Encontrei mais de um aparelho compatível ({listing}). Qual deles você quer {verb}?"
            return f"Encontrei {listing}. Qual deles?"
        return "Encontrei mais de um dispositivo compatível. Qual deles você quer usar?"

    if reason_code in ("action_unresolved", "missing_action"):
        label = targets[0] if targets else clean_mention
        if label:
            return f"Encontrei {label}, mas não entendi o que você quer fazer com ele. Você quer ligar ou desligar?"
        return "Não entendi qual ação você quer fazer. Você quer ligar ou desligar?"

    if reason_code in ("no_compatible_capability", "capability_unsupported"):
        if domain == "light":
            return f"Não encontrei iluminação compatível em {area_name}." if area_name else "Não encontrei iluminação compatível neste cômodo."
        if targets or clean_mention:
            return "Encontrei esse aparelho, mas ele não oferece essa função no Home Assistant."
        return MESSAGES["no_compatible_capability"]

    if reason_code in ("target_not_found", "target_unresolved", "device_not_identified", "missing_target"):
        if clean_mention and area_name and verb:
            return f"Entendi '{clean_mention}', mas não encontrei um dispositivo correspondente em {area_name}. Qual aparelho você quer {verb}?"
        if clean_mention and verb:
            return f"Entendi '{clean_mention}', mas não encontrei um dispositivo correspondente neste cômodo. Qual aparelho você quer {verb}?"
        if clean_mention and area_name:
            return f"Entendi '{clean_mention}', mas não encontrei um dispositivo correspondente em {area_name}."
        if clean_mention:
            return f"Entendi '{clean_mention}', mas não encontrei um dispositivo correspondente neste cômodo. Qual aparelho você quer usar?"
        if verb:
            return f"Entendi que você quer {verb}, mas não identifiquei o aparelho. Qual dispositivo você quer {verb}?"
        return MESSAGES.get(reason_code, MESSAGES["target_not_found"])

    if reason_code in ("backend_unavailable", "queue_saturated", "request_timeout"):
        return "Houve uma falha temporária do serviço ao processar o pedido. Tente novamente em instantes."

    return speech(code or "no_match", reason_code) or MESSAGES["no_match"]


def requested_target(command: dict, original: str) -> str | None:
    """Echo only the failed parsed target, preserving request-local Unicode."""
    mention = command.get("mention")
    if not mention:
        return None
    mention = re.sub(r"^(?:a|o|as|os) ", "", mention)
    if not normalize(mention):
        return None
    area = command.get("area")
    labels = [mention] if not area else [f"{mention} {connector} {area}" for connector in ("de", "do", "da", "dos", "das", "no", "na", "em", "")]
    words = list(re.finditer(r"\w+(?:[\u0300-\u036f]\w*)*", original))
    for label in labels:
        needle = normalize(label).split()
        for start in range(len(words) - len(needle) + 1):
            first, last = words[start], words[start + len(needle) - 1]
            candidate = original[first.start():last.end()]
            if normalize(candidate).split() == needle:
                return candidate
    return labels[0]
