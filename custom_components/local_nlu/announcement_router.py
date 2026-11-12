"""Router and orchestrator for deterministic announcements.

Fast-path executed before LocalNluRuntime.async_process.
Zero neural inferences (Decision / LLM).
"""

from __future__ import annotations

import logging
import re
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
from .announcement_parser import AnnounceIntent, normalize_syntax, parse_announcement
from .announcement_sessions import (
    AnnouncementSessionStore,
    ClarifyMessageSession,
    ConfirmationSession,
    SessionOrigin,
    compute_fingerprint,
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

CONFIRM_AFFIRMATIVE = {"sim", "confirmo", "confirmar", "pode anunciar", "pode mandar", "pode enviar"}
CONFIRM_NEGATIVE = {"nao", "não", "cancela", "cancelar", "deixa pra la", "deixa pra lá", "pare"}

# Operational command words that should discard a clarify session and proceed to normal NLU
_COMMAND_START_WORDS = (
    "apaga", "apague", "desliga", "desligue", "liga", "ligue",
    "abre", "abra", "fecha", "feche", "tranca", "tranque",
    "destranca", "destranque", "toca", "toque", "coloca", "coloque",
    "aumenta", "diminui", "qual", "quanto", "como", "quem",
)


def get_session_store(hass: HomeAssistant) -> AnnouncementSessionStore:
    if DATA_SESSION_STORE not in hass.data:
        hass.data[DATA_SESSION_STORE] = AnnouncementSessionStore()
    return hass.data[DATA_SESSION_STORE]


async def async_maybe_handle_announcement(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    user_input: Any,
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
    store = get_session_store(hass)

    # 1. Resolve identity and origin
    try:
        identity = await resolve_request_identity(hass, user_input, options_dict)
    except IdentityError as id_err:
        # If user explicitly called announcement, return denied instead of falling to neural
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
    origin_kind = "satellite" if getattr(user_input, "satellite_id", None) else (
        "device" if getattr(user_input, "device_id", None) else "text"
    )
    session_origin = SessionOrigin(
        user_id=identity.user_id,
        origin_kind=origin_kind,
        origin_id=origin_id,
        conversation_id=getattr(user_input, "conversation_id", None),
    )

    # 2. Check for active multi-turn session
    active_session = store.get_session(session_origin)
    norm_text = normalize_syntax(raw_text)

    if active_session is not None:
        # User requested cancellation
        if cancellation(raw_text) or norm_text in CONFIRM_NEGATIVE:
            store.pop_session(session_origin)
            return RuntimeResult(
                code="cancelled",
                response_text="Aviso cancelado.",
                provider="deterministic_announcement",
            )

        if isinstance(active_session, ClarifyMessageSession):
            # Check if user started a new operational command instead of supplying message
            if norm_text.startswith(_COMMAND_START_WORDS):
                store.pop_session(session_origin)
                # Fall through to standard NLU to process the operational command
                return None

            # Utterance is treated as the message
            store.pop_session(session_origin)
            return await _execute_delivery(
                hass=hass,
                config_entry=config_entry,
                options=options,
                store=store,
                user_input=user_input,
                identity=identity,
                destination=active_session.destination,
                kind=active_session.kind,
                message=raw_text.strip(),
            )

        if isinstance(active_session, ConfirmationSession):
            store.pop_session(session_origin)
            if norm_text in CONFIRM_AFFIRMATIVE:
                # Revalidate fingerprint to protect against target or config changes
                dest_hash = get_dest_config_hash(options)
                current_fp = compute_fingerprint(
                    active_session.targets,
                    active_session.message,
                    session_origin.user_id,
                    session_origin.origin_id,
                    dest_hash,
                )
                if current_fp != active_session.fingerprint:
                    _LOGGER.warning("Broadcast confirmation fingerprint mismatch; configuration shifted")
                    return RuntimeResult(
                        code="denied",
                        response_text="A configuração dos dispositivos mudou. O aviso não foi enviado.",
                        provider="deterministic_announcement",
                        reason="fingerprint_mismatch",
                    )

                # Convert targets back to DestinationEntity
                targets = tuple(
                    DestinationEntity(
                        kind="assist_satellite" if t.startswith("assist_satellite.") else "media_player",
                        entity_id=t,
                    )
                    for t in active_session.targets
                )
                return await _deliver_to_targets(
                    hass=hass,
                    options=options,
                    store=store,
                    user_input=user_input,
                    identity=identity,
                    targets=targets,
                    message=active_session.message,
                    origin_key=f"{session_origin.user_id}:{session_origin.origin_id}",
                    destination="all",
                )

            # Any reply other than affirmative cancels broadcast
            return RuntimeResult(
                code="cancelled",
                response_text="Aviso cancelado.",
                provider="deterministic_announcement",
            )

    # 3. Parse announcement intent
    intent = parse_announcement(raw_text)
    if intent is None:
        return None

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

    if is_in_quiet_hours(options):
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

    # 6. Handle Reply Intent
    if intent.kind == "reply":
        reply_window = store.pop_reply_window(origin_id)
        if reply_window is None:
            # Also try matching satellite_id or device_id if origin_id was different
            sat_id = getattr(user_input, "satellite_id", None)
            if sat_id:
                reply_window = store.pop_reply_window(sat_id)

        if reply_window is None:
            return RuntimeResult(
                code="unavailable",
                response_text="Não há nenhum recado recente para responder.",
                provider="deterministic_announcement",
                reason="reply_unavailable",
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

        return await _execute_delivery(
            hass=hass,
            config_entry=config_entry,
            options=options,
            store=store,
            user_input=user_input,
            identity=identity,
            destination=reply_window.reply_destination,
            kind="area",
            message=intent.message,
        )

    # 7. Missing message: "avisa na cozinha" -> prompt user
    if not intent.message:
        targets, err = resolve_targets(
            hass=hass,
            destination=intent.destination,
            options=options,
            origin_satellite=getattr(user_input, "satellite_id", None),
            kind=intent.kind,
        )
        if err or not targets:
            return RuntimeResult(
                code="unresolved_target",
                response_text="Não encontrei nenhum dispositivo de aviso configurado para esse cômodo.",
                provider="deterministic_announcement",
                reason=err or "no_destination",
            )

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

    # 8. Broadcast requires confirmation
    if intent.kind == "all" and options.require_confirm_all:
        targets, err = resolve_targets(
            hass=hass,
            destination=intent.destination,
            options=options,
            origin_satellite=getattr(user_input, "satellite_id", None),
            kind="all",
        )
        if err or not targets:
            return RuntimeResult(
                code="unresolved_target",
                response_text="Nenhum dispositivo de aviso disponível para transmissão.",
                provider="deterministic_announcement",
                reason=err or "no_destination",
            )

        target_ids = tuple(t.entity_id for t in targets)
        dest_hash = get_dest_config_hash(options)
        fp = compute_fingerprint(
            target_ids,
            intent.message,
            session_origin.user_id,
            session_origin.origin_id,
            dest_hash,
        )
        store.set_confirmation_session(
            origin=session_origin,
            targets=target_ids,
            message=intent.message,
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

    # 9. Deliver single area or satellite announcement
    return await _execute_delivery(
        hass=hass,
        config_entry=config_entry,
        options=options,
        store=store,
        user_input=user_input,
        identity=identity,
        destination=intent.destination,
        kind=intent.kind,
        message=intent.message,
    )


async def _execute_delivery(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    options: Any,
    store: AnnouncementSessionStore,
    user_input: Any,
    identity: Any,
    destination: str | None,
    kind: str,
    message: str,
) -> RuntimeResult:
    """Resolve targets and deliver message."""
    origin_sat = getattr(user_input, "satellite_id", None)
    targets, err = resolve_targets(
        hass=hass,
        destination=destination,
        options=options,
        origin_satellite=origin_sat,
        kind=kind,
    )
    if err or not targets:
        return RuntimeResult(
            code="unresolved_target",
            response_text="Não encontrei nenhum dispositivo de aviso configurado para esse cômodo.",
            provider="deterministic_announcement",
            reason=err or "no_destination",
        )

    origin_id = request_origin(user_input)
    origin_key = f"{identity.user_id}:{origin_id}"

    return await _deliver_to_targets(
        hass=hass,
        options=options,
        store=store,
        user_input=user_input,
        identity=identity,
        targets=targets,
        message=message,
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
    """Acquire concurrency slot and call delivery engine."""
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
            message=message,
            options=options,
            user=identity.user,
            context=eff_input.context,
        )
    finally:
        await store.release_slot()

    if not delivery.success:
        err_code = delivery.error_code or "announce_failed"
        response_map = {
            "no_destination": "Não encontrei nenhum dispositivo de aviso configurado para esse cômodo.",
            "destination_unavailable": "O dispositivo de destino está indisponível no momento.",
            "permission_denied": "Você não tem permissão para enviar avisos para esse dispositivo.",
            "no_tts_entity": "Nenhum serviço de voz (TTS) foi configurado para reproduzir o aviso.",
            "tts_service_unavailable": "O serviço de voz (TTS) não está disponível no Home Assistant.",
        }
        resp_text = response_map.get(err_code, "Não foi possível enviar o aviso.")
        ha_code = "denied" if err_code == "permission_denied" else (
            "unavailable" if err_code in ("destination_unavailable", "no_tts_entity", "tts_service_unavailable") else "unresolved_target"
        )
        return RuntimeResult(
            code=ha_code,
            response_text=resp_text,
            provider="deterministic_announcement",
            reason=delivery.reason or err_code,
        )

    store.update_cooldown(origin_key)
    dest_str = destination or ""
    store.record_dedup(f"{origin_key}:{dest_str}:{message}", ttl_s=5)

    # Register reply windows for recipients pointing back to sender
    sender_origin = getattr(user_input, "satellite_id", None) or origin_key
    for t in delivery.delivered_targets:
        store.set_reply_window(t, sender_origin, ttl_s=options.session_ttl_s)

    return RuntimeResult(
        code="success",
        response_text="Aviso enviado.",
        provider="deterministic_announcement",
        operation_count=len(delivery.delivered_targets),
    )
