"""Router and orchestrator for deterministic announcements.

Fast-path executed before LocalNluRuntime.async_process.
Zero neural inferences (Decision / LLM).
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .announcement_delivery import (
    DestinationEntity,
    deliver_announcement,
    get_dest_config_hash,
    resolve_targets,
)
from .announcement_options import is_in_quiet_hours, load_announcement_options
from .announcement_parser import (
    normalize_syntax,
    parse_announcement,
    validate_announcement_message,
)
from .announcement_sessions import (
    AnnouncementSessionStore,
    ClarifyMessageSession,
    ConfirmationSession,
    GlobalConcurrencyGate,
    SessionOrigin,
    compute_fingerprint,
)
from .dialogue_coordinator import (
    DialogueScope,
    invalidate_announcement_state,
    invalidate_operational_state,
)
from .identity import (
    IdentityError,
    effective_input,
    request_origin,
    resolve_request_identity,
)
from .runtime import RuntimeResult
from .voice_controls import cancellation

_LOGGER = logging.getLogger(__name__)

DATA_SESSION_STORE = "arandu_announcement_sessions"
DATA_CONCURRENCY_GATE = "arandu_announcement_concurrency_gate"

CONFIRM_AFFIRMATIVE = {"sim", "confirmo", "confirmar", "pode anunciar", "pode mandar", "pode enviar"}
CONFIRM_NEGATIVE = {"nao", "não", "cancela", "cancelar", "deixa pra la", "deixa pra lá", "pare"}

# Operational command words that should discard a clarify/confirm session and proceed to normal NLU
_COMMAND_START_WORDS = (
    "apaga", "apague", "desliga", "desligue", "liga", "ligue",
    "abre", "abra", "fecha", "feche", "tranca", "tranque",
    "destranca", "destranque", "toca", "toque", "coloca", "coloque",
    "aumenta", "diminui", "qual", "quanto", "como", "quem",
    "ativa", "ative", "desativa", "desative", "executa", "execute",
    "adiciona", "adicione", "remove", "remova", "renomeia", "renomeie",
    "troca", "troque", "limpa", "limpe",
)


def get_session_store(hass: HomeAssistant, entry_id: str | None = None) -> AnnouncementSessionStore:
    gate = hass.data.get(DATA_CONCURRENCY_GATE)
    if not isinstance(gate, GlobalConcurrencyGate):
        gate = GlobalConcurrencyGate()
        hass.data[DATA_CONCURRENCY_GATE] = gate
    stores = hass.data.get(DATA_SESSION_STORE)
    if not isinstance(stores, dict):
        # Migrate or initialize entry-scoped dict safely without clobbering other keys
        if isinstance(stores, AnnouncementSessionStore):
            stores = {"_default": stores}
        else:
            stores = {}
        hass.data[DATA_SESSION_STORE] = stores
    key = entry_id or "_default"
    if key not in stores:
        stores[key] = AnnouncementSessionStore(gate=gate)
    return stores[key]


def clear_session_store(hass: HomeAssistant, entry_id: str | None = None) -> None:
    """Clear announcement sessions for the specified config entry on options update or unload."""
    stores = hass.data.get(DATA_SESSION_STORE)
    if isinstance(stores, AnnouncementSessionStore):
        stores.clear()
        return
    if not isinstance(stores, dict):
        return
    if entry_id is None:
        for store in stores.values():
            store.clear()
    else:
        store = stores.get(entry_id)
        if store is not None:
            store.clear()


def _resolve_sender_reply_destination(
    user_input: Any,
    options: Any,
) -> str | None:
    """Resolve an explicit reply destination for the sender, never using internal user:origin strings."""
    sat_id = getattr(user_input, "satellite_id", None)
    if isinstance(sat_id, str) and sat_id.strip():
        return sat_id.strip()
    return None


async def async_maybe_handle_announcement(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    user_input: Any,
    runtime: Any | None = None,
) -> RuntimeResult | None:
    """Intercept and handle announcement requests deterministically.

    Returns None if the utterance is not related to announcements (passing through to NLU).
    Returns RuntimeResult for all recognized announcement intents or errors (no neural fallback).
    """
    raw_text = getattr(user_input, "text", "")
    if not isinstance(raw_text, str) or not raw_text.strip():
        return None

    options_dict = {**config_entry.data, **config_entry.options}
    options = load_announcement_options(options_dict)
    entry_id = getattr(config_entry, "entry_id", None)
    store = get_session_store(hass, entry_id)
    eff_runtime = runtime if runtime is not None else getattr(config_entry, "runtime_data", None)

    # 1. Resolve identity and origin
    try:
        identity = await resolve_request_identity(hass, user_input, options_dict)
    except IdentityError as id_err:
        temp_intent = parse_announcement(raw_text)
        if temp_intent is not None:
            return RuntimeResult(
                code="denied",
                response_text="Identidade não autorizada para enviar avisos.",
                provider="deterministic_announcement",
                reason=id_err.reason,
            )
        return None

    origin_id = request_origin(user_input)
    origin_kind = (
        "satellite"
        if getattr(user_input, "satellite_id", None)
        else ("device" if getattr(user_input, "device_id", None) else "text")
    )
    conv_id = getattr(user_input, "conversation_id", None)
    session_origin = SessionOrigin(
        user_id=identity.user_id,
        origin_kind=origin_kind,
        origin_id=origin_id,
        conversation_id=conv_id,
    )
    scope = DialogueScope(
        user_id=identity.user_id,
        origin_id=origin_id,
        conversation_id=conv_id,
        config_entry_id=entry_id,
    )

    # 2. Check for active multi-turn announcement session
    active_session = store.get_session(session_origin)
    norm_text = normalize_syntax(raw_text)

    if active_session is not None:
        if cancellation(raw_text) or norm_text in CONFIRM_NEGATIVE:
            store.pop_session(session_origin)
            return RuntimeResult(
                code="cancelled",
                response_text="Aviso cancelado.",
                provider="deterministic_announcement",
            )

        if isinstance(active_session, ClarifyMessageSession):
            # Operational command discards clarify session and proceeds to normal NLU
            if norm_text.startswith(_COMMAND_START_WORDS):
                store.pop_session(session_origin)
                return None

            # A new explicit announcement intent replaces the old clarify session
            new_intent = parse_announcement(raw_text)
            if new_intent is not None:
                store.pop_session(session_origin)
                # Fall through to step 3 to process new_intent
            else:
                store.pop_session(session_origin)
                clean_msg, msg_err = validate_announcement_message(raw_text)
                if msg_err or not clean_msg:
                    return RuntimeResult(
                        code="invalid_request",
                        response_text="Mensagem de aviso inválida.",
                        provider="deterministic_announcement",
                        reason=msg_err or "empty_message",
                    )

                invalidate_operational_state(eff_runtime, scope)
                return await _prepare_announcement(
                    hass=hass,
                    options=options,
                    store=store,
                    session_origin=session_origin,
                    user_input=user_input,
                    identity=identity,
                    kind=active_session.kind,
                    destination=active_session.destination,
                    raw_message=clean_msg,
                )

        elif isinstance(active_session, ConfirmationSession):
            # Single-use confirmation: always consume session first
            store.pop_session(session_origin)

            if norm_text in CONFIRM_AFFIRMATIVE:
                if not options.enabled:
                    return RuntimeResult(
                        code="unavailable",
                        response_text="O recurso de avisos por voz está desativado.",
                        provider="deterministic_announcement",
                        reason="announcement_disabled",
                    )
                if not options.allow_all:
                    return RuntimeResult(
                        code="denied",
                        response_text="O envio de avisos para toda a casa está desativado nas configurações.",
                        provider="deterministic_announcement",
                        reason="all_disabled",
                    )
                if is_in_quiet_hours(options, hass=hass):
                    return RuntimeResult(
                        code="denied",
                        response_text="Os avisos estão desativados no horário silencioso.",
                        provider="deterministic_announcement",
                        reason="quiet_hours",
                    )

                clean_msg, msg_err = validate_announcement_message(active_session.message)
                if msg_err or not clean_msg:
                    return RuntimeResult(
                        code="invalid_request",
                        response_text="Mensagem de aviso inválida.",
                        provider="deterministic_announcement",
                        reason=msg_err or "empty_message",
                    )

                # Re-resolve targets dynamically and verify fingerprint against current policy + targets
                resolved_targets, res_err = resolve_targets(
                    hass=hass,
                    destination="all",
                    options=options,
                    origin_satellite=getattr(user_input, "satellite_id", None),
                    kind="all",
                )
                if res_err or not resolved_targets:
                    return RuntimeResult(
                        code="denied",
                        response_text="A configuração dos dispositivos mudou. O aviso não foi enviado.",
                        provider="deterministic_announcement",
                        reason="fingerprint_mismatch",
                    )

                resolved_ids = tuple(t.entity_id for t in resolved_targets)
                dest_hash = get_dest_config_hash(options)
                current_fp = compute_fingerprint(
                    resolved_ids,
                    clean_msg,
                    session_origin.user_id,
                    session_origin.origin_id,
                    dest_hash,
                    conversation_id=session_origin.conversation_id or "",
                )
                if current_fp != active_session.fingerprint:
                    _LOGGER.warning("Broadcast confirmation fingerprint mismatch; policy or targets shifted")
                    return RuntimeResult(
                        code="denied",
                        response_text="A configuração ou política dos dispositivos mudou. O aviso não foi enviado.",
                        provider="deterministic_announcement",
                        reason="fingerprint_mismatch",
                    )

                invalidate_operational_state(eff_runtime, scope)
                return await _deliver_to_targets(
                    hass=hass,
                    options=options,
                    store=store,
                    user_input=user_input,
                    identity=identity,
                    targets=resolved_targets,
                    message=clean_msg,
                    origin_key=f"{session_origin.user_id}:{session_origin.origin_id}",
                    destination="all",
                )

            # Independent command or new announcement between prompt and response
            if norm_text.startswith(_COMMAND_START_WORDS):
                return None

            new_intent = parse_announcement(raw_text)
            if new_intent is None:
                return RuntimeResult(
                    code="cancelled",
                    response_text="Aviso cancelado.",
                    provider="deterministic_announcement",
                )
            # Fall through to handle the new announcement intent

    # 3. Parse announcement intent
    intent = parse_announcement(raw_text)
    if intent is None:
        # Not an announcement; caller (conversation.py) or coordinator invalidates any stale announcement state
        invalidate_announcement_state(hass, scope)
        return None

    # Validated announcement intent interrupts any pending operational session in this scope
    invalidate_operational_state(eff_runtime, scope)

    # Handle adversarial / non-actionable intents deterministically
    if intent.kind == "negation":
        return RuntimeResult(
            code="cancelled",
            response_text="Entendido, aviso cancelado.",
            provider="deterministic_announcement",
            reason="negation",
        )

    if intent.kind == "conditional":
        return RuntimeResult(
            code="invalid_request",
            response_text="Não é possível agendar avisos condicionais.",
            provider="deterministic_announcement",
            reason="conditional_not_supported",
        )

    if intent.kind == "mixed":
        return RuntimeResult(
            code="invalid_request",
            response_text="Não é possível combinar um aviso com outros comandos na mesma frase.",
            provider="deterministic_announcement",
            reason="mixed_plan_not_supported",
        )

    if intent.rejection_reason is not None:
        return RuntimeResult(
            code="invalid_request",
            response_text="Mensagem ou destino de aviso inválido.",
            provider="deterministic_announcement",
            reason=intent.rejection_reason,
        )

    # 4. Check feature toggle and quiet hours
    if not options.enabled:
        return RuntimeResult(
            code="unavailable",
            response_text="O recurso de avisos por voz está desativado.",
            provider="deterministic_announcement",
            reason="announcement_disabled",
        )

    if intent.kind == "all" and not options.allow_all:
        return RuntimeResult(
            code="denied",
            response_text="O envio de avisos para toda a casa está desativado nas configurações.",
            provider="deterministic_announcement",
            reason="all_disabled",
        )

    if is_in_quiet_hours(options, hass=hass):
        return RuntimeResult(
            code="denied",
            response_text="Os avisos estão desativados no horário silencioso.",
            provider="deterministic_announcement",
            reason="quiet_hours",
        )

    # 5. Check deduplication for retries and reconnects before cooldown
    origin_key = f"{session_origin.user_id}:{session_origin.origin_id}"
    if intent.message:
        dest_str = intent.destination or ("all" if intent.kind == "all" else "")
        dedup_key = f"{origin_key}:{dest_str}:{intent.message}"
        if store.is_duplicate(dedup_key):
            return RuntimeResult(
                code="success",
                response_text="Aviso já enviado.",
                provider="deterministic_announcement",
                operation_count=1,
            )

    # 6. Check Cooldown
    if store.check_cooldown(origin_key, options.cooldown_s):
        return RuntimeResult(
            code="unavailable",
            response_text="Aguarde alguns segundos antes de enviar outro aviso.",
            provider="deterministic_announcement",
            reason="announcement_cooldown",
        )

    # 7. Handle Reply Intent
    if intent.kind == "reply":
        reply_key = origin_id
        reply_window = store.get_reply_window(reply_key)
        if reply_window is None:
            sat_id = getattr(user_input, "satellite_id", None)
            if sat_id:
                reply_key = sat_id
                reply_window = store.get_reply_window(reply_key)

        if reply_window is None:
            return RuntimeResult(
                code="unavailable",
                response_text="Não há nenhum recado recente para responder.",
                provider="deterministic_announcement",
                reason="reply_unavailable",
            )

        if not reply_window.reply_destination:
            return RuntimeResult(
                code="unresolved_target",
                response_text="A origem não possui um dispositivo de resposta configurado.",
                provider="deterministic_announcement",
                reason="reply_destination_unavailable",
            )

        if not intent.message:
            store.set_clarify_session(
                origin=session_origin,
                kind="area",
                destination=reply_window.reply_destination,
                ttl_s=options.session_ttl_s,
            )
            return RuntimeResult(
                code="missing_slot",
                response_text="Qual recado você quer responder?",
                continue_conversation=True,
                provider="deterministic_announcement",
            )

        res = await _prepare_announcement(
            hass=hass,
            options=options,
            store=store,
            session_origin=session_origin,
            user_input=user_input,
            identity=identity,
            kind="area",
            destination=reply_window.reply_destination,
            raw_message=intent.message,
        )
        # Consume reply window only once delivery was accepted or attempted past pre-flight
        if res.code == "success" or res.operation_count > 0:
            store.pop_reply_window(reply_key)
        return res

    # 8. Missing message: "avisa na cozinha" / "avisa na casa toda" -> prompt user
    if not intent.message:
        targets, err = resolve_targets(
            hass=hass,
            destination=intent.destination,
            options=options,
            origin_satellite=getattr(user_input, "satellite_id", None),
            kind=intent.kind,
        )
        if err or not targets:
            return _resolution_error_result(err)

        store.set_clarify_session(
            origin=session_origin,
            kind=intent.kind,
            destination=intent.destination or "",
            ttl_s=options.session_ttl_s,
        )
        return RuntimeResult(
            code="missing_slot",
            response_text="Qual recado você quer enviar?",
            continue_conversation=True,
            provider="deterministic_announcement",
        )

    # 9. Unified preparation & routing (handles broadcast confirmation or direct delivery)
    return await _prepare_announcement(
        hass=hass,
        options=options,
        store=store,
        session_origin=session_origin,
        user_input=user_input,
        identity=identity,
        kind=intent.kind,
        destination=intent.destination,
        raw_message=intent.message,
    )


def _resolution_error_result(err: str | None) -> RuntimeResult:
    if err and err.startswith("too_many_targets"):
        count_part = err.split(":", 1)[1] if ":" in err else ""
        msg = (
            f"A transmissão atingiria {count_part} dispositivos, acima do limite configurado."
            if count_part
            else "A transmissão excede o limite máximo de dispositivos configurado."
        )
        return RuntimeResult(
            code="denied",
            response_text=msg,
            provider="deterministic_announcement",
            reason="too_many_targets",
        )
    if err == "ambiguous_destination":
        return RuntimeResult(
            code="unresolved_target",
            response_text="Há mais de um dispositivo ou destino compatível. Especifique qual deseja usar.",
            provider="deterministic_announcement",
            reason="ambiguous_destination",
        )
    return RuntimeResult(
        code="unresolved_target",
        response_text="Não encontrei nenhum dispositivo de aviso configurado para esse cômodo.",
        provider="deterministic_announcement",
        reason=err or "no_destination",
    )


async def _prepare_announcement(
    *,
    hass: HomeAssistant,
    options: Any,
    store: AnnouncementSessionStore,
    session_origin: SessionOrigin,
    user_input: Any,
    identity: Any,
    kind: str,
    destination: str | None,
    raw_message: str,
) -> RuntimeResult:
    """Single unified path: validate message, policy, quiet hours, targets, confirmation or delivery."""
    clean_msg, msg_err = validate_announcement_message(raw_message)
    if msg_err or not clean_msg:
        return RuntimeResult(
            code="invalid_request",
            response_text="Mensagem de aviso inválida.",
            provider="deterministic_announcement",
            reason=msg_err or "empty_message",
        )

    if not options.enabled:
        return RuntimeResult(
            code="unavailable",
            response_text="O recurso de avisos por voz está desativado.",
            provider="deterministic_announcement",
            reason="announcement_disabled",
        )

    if kind == "all" and not options.allow_all:
        return RuntimeResult(
            code="denied",
            response_text="O envio de avisos para toda a casa está desativado nas configurações.",
            provider="deterministic_announcement",
            reason="all_disabled",
        )

    if is_in_quiet_hours(options, hass=hass):
        return RuntimeResult(
            code="denied",
            response_text="Os avisos estão desativados no horário silencioso.",
            provider="deterministic_announcement",
            reason="quiet_hours",
        )

    targets, err = resolve_targets(
        hass=hass,
        destination=destination,
        options=options,
        origin_satellite=getattr(user_input, "satellite_id", None),
        kind=kind,
    )
    if err or not targets:
        return _resolution_error_result(err)

    # Broadcast always requires confirmation when require_confirm_all is enabled (hard policy)
    if kind == "all" and options.require_confirm_all:
        target_ids = tuple(t.entity_id for t in targets)
        dest_hash = get_dest_config_hash(options)
        fp = compute_fingerprint(
            target_ids,
            clean_msg,
            session_origin.user_id,
            session_origin.origin_id,
            dest_hash,
            conversation_id=session_origin.conversation_id or "",
        )
        store.set_confirmation_session(
            origin=session_origin,
            targets=target_ids,
            message=clean_msg,
            fingerprint=fp,
            ttl_s=options.session_ttl_s,
        )
        count = len(targets)
        noun = "dispositivo" if count == 1 else "dispositivos"
        return RuntimeResult(
            code="confirmation_required",
            response_text=f"Quer anunciar esse recado em {count} {noun}?",
            continue_conversation=True,
            provider="deterministic_announcement",
        )

    origin_key = f"{session_origin.user_id}:{session_origin.origin_id}"
    return await _deliver_to_targets(
        hass=hass,
        options=options,
        store=store,
        user_input=user_input,
        identity=identity,
        targets=targets,
        message=clean_msg,
        origin_key=origin_key,
        destination=destination,
    )


async def _deliver_to_targets(
    hass: HomeAssistant,
    options: Any,
    store: AnnouncementSessionStore,
    user_input: Any,
    identity: Any,
    targets: tuple[DestinationEntity, ...],
    message: str,
    origin_key: str,
    destination: str | None = None,
) -> RuntimeResult:
    """Validate message, acquire concurrency slot, and call delivery engine."""
    clean_msg, msg_err = validate_announcement_message(message)
    if msg_err or not clean_msg:
        return RuntimeResult(
            code="invalid_request",
            response_text="Mensagem de aviso inválida.",
            provider="deterministic_announcement",
            reason=msg_err or "empty_message",
        )

    if not await store.acquire_slot():
        return RuntimeResult(
            code="unavailable",
            response_text="O sistema de avisos está ocupado no momento. Tente novamente em instantes.",
            provider="deterministic_announcement",
            reason="announcement_busy",
        )

    try:
        eff_input = effective_input(user_input, identity)
        delivery = await deliver_announcement(
            hass=hass,
            targets=targets,
            message=clean_msg,
            options=options,
            user=identity.user,
            context=eff_input.context,
        )
    finally:
        await store.release_slot()

    if not delivery.success:
        if delivery.delivered_targets:
            # Partial delivery: record cooldown and reply windows ONLY for accepted targets;
            # never record dedup as full success so retry isn't falsely treated as complete.
            store.update_cooldown(origin_key)
            sender_dest = _resolve_sender_reply_destination(user_input, options)
            if sender_dest:
                for t in delivery.delivered_targets:
                    store.set_reply_window(t, sender_dest, ttl_s=options.session_ttl_s)
            failed_str = ", ".join(delivery.failed_targets)
            return RuntimeResult(
                code="unavailable",
                response_text=(
                    f"Aviso aceito pelo Home Assistant apenas em {len(delivery.delivered_targets)} "
                    f"de {delivery.total_targets} dispositivos; falhou em {failed_str}."
                ),
                provider="deterministic_announcement",
                operation_count=len(delivery.delivered_targets),
                reason=delivery.error_code or "partial_delivery",
            )

        err_code = delivery.error_code or "announce_failed"
        response_map = {
            "no_destination": "Não encontrei nenhum dispositivo de aviso configurado para esse cômodo.",
            "destination_unavailable": "O dispositivo de destino está indisponível no momento.",
            "permission_denied": "Você não tem permissão para enviar avisos para esse dispositivo.",
            "no_tts_entity": "Nenhum serviço de voz (TTS) foi configurado para reproduzir o aviso.",
            "tts_service_unavailable": "O serviço de voz (TTS) não está disponível no Home Assistant.",
            "announcement_fallback_unavailable": "O dispositivo não suporta avisos diretos e não possui media player vinculado.",
        }
        resp_text = response_map.get(err_code, "Não foi possível enviar o aviso.")
        ha_code = (
            "denied"
            if err_code == "permission_denied"
            else (
                "unavailable"
                if err_code
                in (
                    "destination_unavailable",
                    "no_tts_entity",
                    "tts_service_unavailable",
                    "announcement_fallback_unavailable",
                    "announce_failed",
                )
                else "unresolved_target"
            )
        )
        return RuntimeResult(
            code=ha_code,
            response_text=resp_text,
            provider="deterministic_announcement",
            reason=delivery.reason or err_code,
        )

    store.update_cooldown(origin_key)
    dest_str = destination or ""
    store.record_dedup(f"{origin_key}:{dest_str}:{clean_msg}", ttl_s=5)

    sender_dest = _resolve_sender_reply_destination(user_input, options)
    if sender_dest:
        for t in delivery.delivered_targets:
            store.set_reply_window(t, sender_dest, ttl_s=options.session_ttl_s)

    return RuntimeResult(
        code="success",
        response_text="Aviso enviado ao Home Assistant para reprodução (reprodução audível não confirmada).",
        provider="deterministic_announcement",
        operation_count=len(delivery.delivered_targets),
        reason=delivery.delivery_status,
    )
