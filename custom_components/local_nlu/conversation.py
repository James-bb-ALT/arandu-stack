"""Home Assistant conversation entity for Local NLU."""

from __future__ import annotations

from typing import Any, Literal

try:
    from typing import override
except ImportError:

    def override(function: Any) -> Any:
        return function

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .runtime import RuntimeResult


async def async_setup_entry(
    _: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities((LocalNluConversationEntity(config_entry),))


class LocalNluConversationEntity(
    conversation.ConversationEntity,
    conversation.AbstractConversationAgent,
):
    """Process one PT-BR utterance without retaining it."""

    _attr_has_entity_name = True
    _attr_name = "Arandu Assistant"
    _attr_supported_features = conversation.ConversationEntityFeature.CONTROL

    def __init__(self, config_entry: ConfigEntry) -> None:
        super().__init__()
        self._runtime = config_entry.runtime_data
        self._entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}-conversation"

    @property
    @override
    def supported_languages(self) -> list[str] | Literal["*"]:
        return ["pt-BR"]

    @override
    async def async_process(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult:
        from .stack import prepare
        from .request_trace import capture, observe
        options = {**self._entry.data, **self._entry.options}
        with capture(self.hass, self._runtime, options, user_input) as trace:
            prepared, problem = await observe('prepare', prepare(self.hass, self._entry, user_input))
            result = problem if problem is not None else await self._runtime.async_process(prepared)
            trace.finish(result, user_input.text, trace.include_text)
        response = intent.IntentResponse(language=user_input.language)
        speech, error_code, query = _render(result)
        if error_code is None:
            response.async_set_speech(speech)
            if query:
                response.response_type = intent.IntentResponseType.QUERY_ANSWER
        else:
            response.async_set_error(error_code, speech)
        values = {"response": response, "conversation_id": result.conversation_id or user_input.conversation_id}
        fields = getattr(conversation.ConversationResult, "__dataclass_fields__", None)
        if fields is None or "continue_conversation" in fields:
            values["continue_conversation"] = result.continue_conversation or result.code == "missing_slot"
        return conversation.ConversationResult(**values)


def _render(
    result: RuntimeResult,
) -> tuple[str, intent.IntentResponseErrorCode | None, bool]:
    from .contextual_errors import speech as failure_speech
    if result.response_text is None and (message := failure_speech(result.code, result.reason)) is not None:
        return message, intent.IntentResponseErrorCode.UNKNOWN, False
    if result.response_text is not None:
        error = None if result.code in ("success", "query_success", "missing_slot", "confirmation_required", "cancelled") else intent.IntentResponseErrorCode.UNKNOWN
        return result.response_text, error, result.code == "query_success"
    if result.code == "success":
        if result.operation_count == 1:
            return ("Pronto.", None, False)
        return (
            f"Pronto, executei {result.operation_count} comandos.",
            None,
            False,
        )
    if result.code == "query_success":
        values = "; ".join(_render_state(state) for state in result.states)
        return (f"{values}.", None, True)
    if result.code == "ambiguous":
        return (
            "Encontrei mais de um alvo. Diga o nome ou a área com mais detalhes.",
            intent.IntentResponseErrorCode.NO_VALID_TARGETS,
            False,
        )
    if result.code == "missing_slot":
        if result.missing_slot == "media_query" and result.provider:
            return (
                f"O que você quer ouvir no {result.provider}?",
                None,
                False,
            )
        if result.missing_slot == "media_query":
            return ("O que você quer ouvir?", None, False)
        if result.missing_slot == "player":
            return ("Em qual cômodo você quer tocar?", None, False)
        return ("Qual alvo você quer usar?", None, False)
    if result.code == "unresolved_target":
        return (
            "Não encontrei esse alvo. Qual alvo você quer usar?",
            intent.IntentResponseErrorCode.NO_VALID_TARGETS,
            False,
        )
    if result.code == "unsupported_intent":
        return (
            "Não entendi esse comando.",
            intent.IntentResponseErrorCode.NO_INTENT_MATCH,
            False,
        )
    if result.code in ("no_match", "invalid_request"):
        return (
            "Não entendi esse comando.",
            intent.IntentResponseErrorCode.NO_INTENT_MATCH,
            False,
        )
    if result.code == "denied":
        return (
            "Você não tem permissão para fazer isso.",
            intent.IntentResponseErrorCode.UNKNOWN,
            False,
        )
    if result.code == "stale":
        return (
            "O alvo mudou ou está indisponível. Tente novamente.",
            intent.IntentResponseErrorCode.NO_VALID_TARGETS,
            False,
        )
    return (
        "O ARANDU NLU não conseguiu concluir o comando.",
        intent.IntentResponseErrorCode.UNKNOWN,
        False,
    )


def _render_state(state: Any) -> str:
    value = {"off": "desligado", "on": "ligado"}.get(state.state, state.state)
    suffix = f" {state.unit}" if state.unit is not None else ""
    return f"{state.label}: {value}{suffix}"
