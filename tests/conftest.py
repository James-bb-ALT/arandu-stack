"""Global pytest test configuration for Arandu Stack integration tests."""
from dataclasses import dataclass
from pathlib import Path
import sys
import types
from typing import Any

# Ensure test harness fakes from mlp are loaded
sys.path.append(str(Path(__file__).parents[1] / "arandu_stack/rootfs/modules/arandu_nlu/rootfs/src/tests/mlp"))
try:
    from test_catalog_runtime import _install_home_assistant_fakes
    _install_home_assistant_fakes()
except Exception:
    pass

if "homeassistant.config_entries" not in sys.modules:
    ce_mod = types.ModuleType("homeassistant.config_entries")
    class _ConfigEntry:
        pass
    ce_mod.ConfigEntry = _ConfigEntry
    sys.modules["homeassistant.config_entries"] = ce_mod
    if "homeassistant" in sys.modules:
        sys.modules["homeassistant"].config_entries = ce_mod
    core_mod = sys.modules.get("homeassistant.core") or types.ModuleType("homeassistant.core")
    class _Context:
        def __init__(self, user_id: str | None = None, parent_id: str | None = None, id: str | None = None) -> None:
            self.user_id = user_id
            self.parent_id = parent_id
            self.id = id or "ctx_test"
    core_mod.Context = _Context
    core_mod.HomeAssistant = Any
    core_mod.callback = lambda f: f
    sys.modules["homeassistant.core"] = core_mod
    if "homeassistant" in sys.modules:
        sys.modules["homeassistant"].core = core_mod

if "homeassistant.components.conversation" not in sys.modules:
    conv_mod = types.ModuleType("homeassistant.components.conversation")
    class _ConvEntity:
        def __init__(self) -> None:
            self.hass = None
    class _ConvAgent:
        pass
    class _ConvFeature:
        CONTROL = 1
    @dataclass
    class _ConvResult:
        response: Any
        conversation_id: str | None = None
        continue_conversation: bool = False
    @dataclass
    class _ConvInput:
        text: str
        context: Any
        conversation_id: str | None = None
        device_id: str | None = None
        language: str = "pt-BR"
        satellite_id: str | None = None

    conv_mod.ConversationEntity = _ConvEntity
    conv_mod.AbstractConversationAgent = _ConvAgent
    conv_mod.ConversationEntityFeature = _ConvFeature
    conv_mod.ConversationResult = _ConvResult
    conv_mod.ConversationInput = _ConvInput
    sys.modules["homeassistant.components.conversation"] = conv_mod
    if "homeassistant.components" in sys.modules:
        sys.modules["homeassistant.components"].conversation = conv_mod

if "homeassistant.helpers.entity_platform" not in sys.modules:
    ep_mod = types.ModuleType("homeassistant.helpers.entity_platform")
    ep_mod.AddConfigEntryEntitiesCallback = Any
    sys.modules["homeassistant.helpers.entity_platform"] = ep_mod
    if "homeassistant.helpers" in sys.modules:
        sys.modules["homeassistant.helpers"].entity_platform = ep_mod

if "homeassistant.helpers.intent" not in sys.modules:
    intent_mod = types.ModuleType("homeassistant.helpers.intent")
    class _IntentErrorCode:
        UNKNOWN = "unknown"
        NO_VALID_TARGETS = "no_valid_targets"
        NO_INTENT_MATCH = "no_intent_match"
    class _IntentResponseType:
        QUERY_ANSWER = "query_answer"
    class _IntentResponse:
        def __init__(self, language: str = "pt-BR") -> None:
            self.language = language
            self.speech = {}
            self.response_type = None
            self.error_code = None
        def async_set_speech(self, speech: str) -> None:
            self.speech = {"plain": {"speech": speech}}
        def async_set_error(self, code: str, speech: str) -> None:
            self.error_code = code
            self.speech = {"plain": {"speech": speech}}

    intent_mod.IntentResponseErrorCode = _IntentErrorCode
    intent_mod.IntentResponseType = _IntentResponseType
    intent_mod.IntentResponse = _IntentResponse
    sys.modules["homeassistant.helpers.intent"] = intent_mod
    if "homeassistant.helpers" in sys.modules:
        sys.modules["homeassistant.helpers"].intent = intent_mod

