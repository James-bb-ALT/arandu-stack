"""Phase 6 comprehensive test suite for deterministic voice announcements (Intercom).

Verifies AST parsing, options validation, destination resolution, permissions,
multi-turn sessions, broadcast confirmation, concurrency, and 0 neural model calls.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import AsyncMock, patch

# Ensure test harness fakes are installed
sys.path.append(str(Path(__file__).parents[1] / "arandu_stack/rootfs/modules/arandu_nlu/rootfs/src/tests/mlp"))
from test_catalog_runtime import _install_home_assistant_fakes, _Entry, _State, _Areas, _Devices

_install_home_assistant_fakes()

# Install conversation and intent fakes for LocalNluConversationEntity
import types
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
    sys.modules["homeassistant.components"].conversation = conv_mod

if "homeassistant.helpers.intent" not in sys.modules:
    intent_mod = types.ModuleType("homeassistant.helpers.intent")
    class _IntentErrorCode:
        UNKNOWN = "unknown"
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
    sys.modules["homeassistant.helpers"].intent = intent_mod

if "homeassistant.helpers.entity_platform" not in sys.modules:
    ep_mod = types.ModuleType("homeassistant.helpers.entity_platform")
    ep_mod.AddConfigEntryEntitiesCallback = Any
    sys.modules["homeassistant.helpers.entity_platform"] = ep_mod
    sys.modules["homeassistant.helpers"].entity_platform = ep_mod

if "homeassistant.core" not in sys.modules or not hasattr(sys.modules["homeassistant.core"], "callback"):
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
    sys.modules["homeassistant"].core = core_mod

class FakeFlow:
    def __init_subclass__(cls, **kwargs):
        pass
    def __init__(self):
        self.context, self.entries = {}, []
    def _async_current_entries(self):
        return self.entries
    async def async_set_unique_id(self, value):
        self.unique_id = value
    def _abort_if_unique_id_configured(self):
        pass
    def async_abort(self, **kwargs):
        return {"type": "abort", **kwargs}
    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}
    def async_show_menu(self, **kwargs):
        return {"type": "menu", **kwargs}
    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}

if "homeassistant.config_entries" not in sys.modules or not hasattr(sys.modules["homeassistant.config_entries"], "OptionsFlow"):
    ce_mod = sys.modules.get("homeassistant.config_entries") or types.ModuleType("homeassistant.config_entries")
    ce_mod.ConfigEntry = Any
    ce_mod.ConfigFlow = ce_mod.OptionsFlow = FakeFlow
    sys.modules["homeassistant.config_entries"] = ce_mod

if "homeassistant.helpers.selector" not in sys.modules:
    sel_mod = types.ModuleType("homeassistant.helpers.selector")
    class _Sel:
        def __init__(self, *args, **kwargs):
            pass
    sel_mod.AreaSelector = sel_mod.EntitySelector = sel_mod.SelectSelector = sel_mod.DeviceSelector = sel_mod.ObjectSelector = _Sel
    sel_mod.EntitySelectorConfig = sel_mod.SelectSelectorConfig = _Sel
    class _Mode:
        DROPDOWN = "dropdown"
    sel_mod.SelectSelectorMode = _Mode
    sys.modules["homeassistant.helpers.selector"] = sel_mod
    sys.modules["homeassistant.helpers"].selector = sel_mod

if "homeassistant.helpers.aiohttp_client" not in sys.modules:
    aio_mod = types.ModuleType("homeassistant.helpers.aiohttp_client")
    aio_mod.async_get_clientsession = lambda hass: None
    sys.modules["homeassistant.helpers.aiohttp_client"] = aio_mod
    sys.modules["homeassistant.helpers"].aiohttp_client = aio_mod

from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant

from custom_components.local_nlu.announcement_delivery import (
    DestinationEntity,
    deliver_announcement,
    resolve_targets,
)
from custom_components.local_nlu.announcement_options import (
    AnnouncementOptions,
    CONF_ANNOUNCEMENTS_ALLOW_ALL,
    CONF_ANNOUNCEMENTS_CHIME,
    CONF_ANNOUNCEMENTS_COOLDOWN_S,
    CONF_ANNOUNCEMENTS_DESTINATIONS,
    CONF_ANNOUNCEMENTS_ENABLED,
    CONF_ANNOUNCEMENTS_MAX_TARGETS,
    CONF_ANNOUNCEMENTS_TTS_ENTITY,
    load_announcement_options,
)
from custom_components.local_nlu.announcement_parser import AnnounceIntent, parse_announcement
from custom_components.local_nlu.announcement_router import (
    async_maybe_handle_announcement,
    get_session_store,
)
from custom_components.local_nlu.announcement_sessions import (
    AnnouncementSessionStore,
    SessionOrigin,
)
from custom_components.local_nlu.conversation import LocalNluConversationEntity
from custom_components.local_nlu.runtime import RuntimeResult


class MockPermissions:
    def __init__(self, allowed_entities: set[str] | None = None) -> None:
        self.allowed = allowed_entities

    def check_entity(self, entity_id: str, policy: str) -> bool:
        if self.allowed is None:
            return True
        return entity_id in self.allowed


class MockUser:
    def __init__(self, user_id: str = "user_test", is_active: bool = True, allowed_entities: set[str] | None = None) -> None:
        self.id = user_id
        self.is_active = is_active
        self.name = "Test User"
        self.permissions = MockPermissions(allowed_entities)


class MockServices:
    def __init__(self) -> None:
        self.available = {
            ("assist_satellite", "announce"),
            ("tts", "speak"),
            ("light", "turn_off"),
            ("light", "turn_on"),
            ("lock", "unlock"),
        }
        self.calls: list[dict] = []

    def has_service(self, domain: str, service: str) -> bool:
        return (domain, service) in self.available

    async def async_call(self, domain: str, service: str, data: dict, blocking: bool = True, context: Any = None, target: dict | None = None) -> None:
        self.calls.append({
            "domain": domain,
            "service": service,
            "data": data,
            "target": target or {},
            "context": context,
        })


class MockAreaRegistry:
    def __init__(self) -> None:
        self._areas = {
            "area_cozinha": SimpleNamespace(id="area_cozinha", name="Cozinha"),
            "area_sala": SimpleNamespace(id="area_sala", name="Sala"),
            "area_quarto": SimpleNamespace(id="area_quarto", name="Quarto"),
            "area_escritorio": SimpleNamespace(id="area_escritorio", name="Escritório"),
        }

    def async_get_area(self, area_id: str) -> Any:
        return self._areas.get(area_id)

    def async_list_areas(self) -> list[Any]:
        return list(self._areas.values())


class MockEntityRegistry:
    def __init__(self, entries: dict[str, Any]) -> None:
        self.entities = entries


class MockAuth:
    def __init__(self, users: dict[str, MockUser]) -> None:
        self._users = users

    async def async_get_user(self, user_id: str) -> MockUser | None:
        return self._users.get(user_id)


def create_mock_hass() -> HomeAssistant:
    hass = SimpleNamespace()
    hass.data = {}
    hass.exposed = {
        "assist_satellite.cozinha",
        "assist_satellite.quarto",
        "media_player.sala",
        "tts.piper",
        "light.cozinha",
        "lock.porta",
    }
    hass.area_registry = MockAreaRegistry()
    hass.device_registry = _Devices()
    hass.entity_registry = MockEntityRegistry({
        "assist_satellite.cozinha": _Entry(id="reg_sat_cozinha", entity_id="assist_satellite.cozinha", area_id="area_cozinha", aliases=[], name="Satélite Cozinha"),
        "assist_satellite.quarto": _Entry(id="reg_sat_quarto", entity_id="assist_satellite.quarto", area_id="area_quarto", aliases=[], name="Satélite Quarto"),
        "media_player.sala": _Entry(id="reg_mp_sala", entity_id="media_player.sala", area_id="area_sala", aliases=[], name="Caixa Sala"),
        "tts.piper": _Entry(id="reg_tts", entity_id="tts.piper", area_id=None, aliases=[], name="Piper TTS"),
        "light.cozinha": _Entry(id="reg_light", entity_id="light.cozinha", area_id="area_cozinha", aliases=[], name="Luz Cozinha"),
        "lock.porta": _Entry(id="reg_lock", entity_id="lock.porta", area_id="area_sala", aliases=[], name="Fechadura"),
    })
    hass.states = SimpleNamespace(
        get=lambda eid: _State(state="idle" if eid.startswith("media_player") else "on", attributes={})
        if eid in hass.exposed else None
    )
    hass.services = MockServices()
    user = MockUser("user_test")
    hass.auth = MockAuth({"user_test": user})
    return hass


def make_user_input(text: str, satellite_id: str | None = None, conversation_id: str = "conv_1", user_id: str = "user_test") -> conversation.ConversationInput:
    ctx = Context(user_id=user_id)
    return conversation.ConversationInput(
        text=text,
        context=ctx,
        conversation_id=conversation_id,
        device_id=None,
        language="pt-BR",
        satellite_id=satellite_id,
    )


class TestPhase6AnnouncementParser(unittest.TestCase):
    """Test 100% deterministic parser and AST generation."""

    def test_basic_patterns(self):
        cases = [
            ("avisa na cozinha que o almoço está pronto", "area", "cozinha", "o almoço está pronto"),
            ("fala no quarto que já pode vir", "area", "quarto", "já pode vir"),
            ("anuncia na sala: a reunião começou", "area", "sala", "a reunião começou"),
            ("manda um recado pro escritório dizendo que estou chegando", "area", "escritório", "estou chegando"),
            ("diz no alto-falante da cozinha que acabou o café", "satellite", "alto-falante da cozinha", "acabou o café"),
            ("manda uma mensagem para o satélite do quarto que terminou", "satellite", "satélite do quarto", "terminou"),
        ]
        for utterance, kind, dest, msg in cases:
            intent = parse_announcement(utterance)
            self.assertIsNotNone(intent, f"Failed on: {utterance}")
            self.assertEqual(intent.kind, kind)
            self.assertEqual(intent.destination.lower(), dest.lower())
            self.assertEqual(intent.message, msg)
            self.assertFalse(intent.requires_confirmation)

    def test_broadcast_patterns(self):
        for phrase in [
            "avisa na casa toda que vamos sair",
            "anuncia em todos os cômodos que chegou a encomenda",
            "fala na casa inteira que o almoço está pronto",
        ]:
            intent = parse_announcement(phrase)
            self.assertIsNotNone(intent)
            self.assertEqual(intent.kind, "all")
            self.assertTrue(intent.requires_confirmation)

    def test_missing_message(self):
        intent = parse_announcement("avisa na cozinha")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.kind, "area")
        self.assertEqual(intent.destination.lower(), "cozinha")
        self.assertIsNone(intent.message)

    def test_reply_pattern(self):
        intent = parse_announcement("responde que já vou")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.kind, "reply")
        self.assertEqual(intent.message, "já vou")

    def test_adversarial_text_with_command_content_is_pure_text(self):
        # "fala na sala que desliga a luz da cozinha" -> pure text!
        intent = parse_announcement("fala na sala que desliga a luz da cozinha")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.kind, "area")
        self.assertEqual(intent.message, "desliga a luz da cozinha")

        # "avisa no quarto que destranca a porta" -> pure text!
        intent = parse_announcement("avisa no quarto que destranca a porta")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.kind, "area")
        self.assertEqual(intent.message, "destranca a porta")

    def test_negation_and_conditional_and_mixed(self):
        # Negation
        intent = parse_announcement("não avisa na cozinha que cheguei")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.kind, "negation")

        # Conditional
        intent = parse_announcement("se eu chegar, avisa na cozinha")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.kind, "conditional")

        # Mixed
        intent = parse_announcement("avisa na cozinha e apaga a luz")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.kind, "mixed")

    def test_non_announcements_pass_through(self):
        cases = [
            "fala comigo",
            "me fala a temperatura da cozinha",
            "anuncia o clima",
            "pode falar mais alto",
            "me lembra de comprar pão",
            "remove leite da lista",
            "liga a luz da sala",
        ]
        for phrase in cases:
            intent = parse_announcement(phrase)
            self.assertIsNone(intent, f"Should return None for: {phrase}")

    def test_safety_and_limits(self):
        # Jinja
        self.assertIsNotNone(parse_announcement("avisa na cozinha que {{ states.light }}").rejection_reason)
        # HTML / SSML
        self.assertIsNotNone(parse_announcement("avisa na cozinha que <speak>teste</speak>").rejection_reason)
        # URL
        self.assertIsNotNone(parse_announcement("avisa na cozinha que veja em http://site.com").rejection_reason)
        # Oversized
        self.assertIsNotNone(parse_announcement("avisa na cozinha que " + ("x" * 281)).rejection_reason)


class TestPhase6AnnouncementExecution(unittest.IsolatedAsyncioTestCase):
    """Execution and routing integration tests with mock HA."""

    def setUp(self):
        self.hass = create_mock_hass()
        self.options = {
            CONF_ANNOUNCEMENTS_ENABLED: True,
            CONF_ANNOUNCEMENTS_ALLOW_ALL: True,
            CONF_ANNOUNCEMENTS_CHIME: True,
            CONF_ANNOUNCEMENTS_COOLDOWN_S: 3,
            CONF_ANNOUNCEMENTS_TTS_ENTITY: "tts.piper",
            CONF_ANNOUNCEMENTS_DESTINATIONS: {
                "cozinha": [{"kind": "assist_satellite", "entity_id": "assist_satellite.cozinha"}],
                "quarto": [{"kind": "assist_satellite", "entity_id": "assist_satellite.quarto"}],
                "sala": [{"kind": "media_player", "entity_id": "media_player.sala"}],
            },
        }
        self.entry = SimpleNamespace(
            data={},
            options=self.options,
            entry_id="entry_test",
        )
        get_session_store(self.hass).clear()

    async def test_case_1_announce_to_kitchen_only_assist_satellite(self):
        """Case 1: 'Avisa na cozinha que o almoço está pronto' -> only assist_satellite.announce on kitchen."""
        inp = make_user_input("Avisa na cozinha que o almoço está pronto")
        result = await async_maybe_handle_announcement(self.hass, self.entry, inp)

        self.assertIsNotNone(result)
        self.assertEqual(result.code, "success")
        self.assertEqual(len(self.hass.services.calls), 1)

        call = self.hass.services.calls[0]
        self.assertEqual(call["domain"], "assist_satellite")
        self.assertEqual(call["service"], "announce")
        self.assertEqual(call["data"]["message"], "o almoço está pronto")
        self.assertEqual(call["data"]["preannounce"], True)
        self.assertEqual(call["target"]["entity_id"], ["assist_satellite.cozinha"])

    async def test_case_2_fala_na_sala_pure_speech_zero_light_operation(self):
        """Case 2: 'Fala na sala que a luz do quarto está apagada' -> only speech, zero light operation."""
        inp = make_user_input("Fala na sala que a luz do quarto está apagada")
        result = await async_maybe_handle_announcement(self.hass, self.entry, inp)

        self.assertIsNotNone(result)
        self.assertEqual(result.code, "success")
        self.assertEqual(len(self.hass.services.calls), 1)

        call = self.hass.services.calls[0]
        # sala has media_player, so uses tts.speak
        self.assertEqual(call["domain"], "tts")
        self.assertEqual(call["service"], "speak")
        self.assertEqual(call["data"]["media_player_entity_id"], "media_player.sala")
        self.assertEqual(call["data"]["message"], "a luz do quarto está apagada")

        # ZERO light calls!
        for c in self.hass.services.calls:
            self.assertNotEqual(c["domain"], "light")

    async def test_case_3_diz_no_quarto_destranca_porta_zero_lock_operation(self):
        """Case 3: 'Diz no quarto que destranca a porta' -> audio only, zero lock.unlock."""
        inp = make_user_input("Diz no quarto que destranca a porta")
        result = await async_maybe_handle_announcement(self.hass, self.entry, inp)

        self.assertIsNotNone(result)
        self.assertEqual(result.code, "success")
        self.assertEqual(len(self.hass.services.calls), 1)

        call = self.hass.services.calls[0]
        self.assertEqual(call["domain"], "assist_satellite")
        self.assertEqual(call["service"], "announce")
        self.assertEqual(call["data"]["message"], "destranca a porta")

        # ZERO lock calls!
        for c in self.hass.services.calls:
            self.assertNotEqual(c["domain"], "lock")

    async def test_case_4_broadcast_requires_confirmation(self):
        """Case 4: 'Anuncia em todos os cômodos que vamos sair' requires confirmation."""
        inp = make_user_input("Anuncia em todos os cômodos que vamos sair")
        result = await async_maybe_handle_announcement(self.hass, self.entry, inp)

        self.assertIsNotNone(result)
        self.assertEqual(result.code, "confirmation_required")
        self.assertTrue(result.continue_conversation)
        self.assertEqual(len(self.hass.services.calls), 0)  # Zero delivery before confirmation!

        # Confirm with "sim"
        inp_confirm = make_user_input("sim")
        result_confirm = await async_maybe_handle_announcement(self.hass, self.entry, inp_confirm)

        self.assertIsNotNone(result_confirm)
        self.assertEqual(result_confirm.code, "success")
        # Broadcast delivered to all 3 devices
        self.assertEqual(len(self.hass.services.calls), 3)

    async def test_case_4_broadcast_blocked_when_allow_all_is_off(self):
        """Case 4b: Broadcast blocked by default when allow_all is False."""
        self.entry.options[CONF_ANNOUNCEMENTS_ALLOW_ALL] = False
        inp = make_user_input("Anuncia na casa toda que vamos sair")
        result = await async_maybe_handle_announcement(self.hass, self.entry, inp)

        self.assertIsNotNone(result)
        self.assertEqual(result.code, "denied")
        self.assertEqual(len(self.hass.services.calls), 0)

    async def test_case_5_and_6_multi_turn_prompt_message_then_deliver(self):
        """Case 5 & 6: 'Avisa no quarto' asks message, then 'Cheguei!' delivers once."""
        inp1 = make_user_input("Avisa no quarto")
        result1 = await async_maybe_handle_announcement(self.hass, self.entry, inp1)

        self.assertIsNotNone(result1)
        self.assertEqual(result1.code, "missing_slot")
        self.assertEqual(result1.response_text, "Qual recado você quer enviar?")
        self.assertTrue(result1.continue_conversation)
        self.assertEqual(len(self.hass.services.calls), 0)

        # Multi-turn turn 2: "Cheguei!"
        inp2 = make_user_input("Cheguei!")
        result2 = await async_maybe_handle_announcement(self.hass, self.entry, inp2)

        self.assertIsNotNone(result2)
        self.assertEqual(result2.code, "success")
        self.assertEqual(len(self.hass.services.calls), 1)
        self.assertEqual(self.hass.services.calls[0]["target"]["entity_id"], ["assist_satellite.quarto"])
        self.assertEqual(self.hass.services.calls[0]["data"]["message"], "Cheguei!")

    async def test_case_7_multi_turn_cancel(self):
        """Case 7: 'Avisa no quarto' -> 'Cancela' -> zero delivery and context wiped."""
        inp1 = make_user_input("Avisa no quarto")
        await async_maybe_handle_announcement(self.hass, self.entry, inp1)

        inp2 = make_user_input("Cancela")
        result2 = await async_maybe_handle_announcement(self.hass, self.entry, inp2)

        self.assertIsNotNone(result2)
        self.assertEqual(result2.code, "cancelled")
        self.assertEqual(len(self.hass.services.calls), 0)

    async def test_case_8_negation_zero_effect(self):
        """Case 8: 'Não avisa no quarto que cheguei' -> refuses, zero effect."""
        inp = make_user_input("Não avisa no quarto que cheguei")
        result = await async_maybe_handle_announcement(self.hass, self.entry, inp)

        self.assertIsNotNone(result)
        self.assertEqual(result.code, "cancelled")
        self.assertEqual(len(self.hass.services.calls), 0)

    async def test_case_9_conditional_zero_effect(self):
        """Case 9: 'Se eu chegar, avisa na cozinha' -> refuses conditional, zero effect."""
        inp = make_user_input("Se eu chegar, avisa na cozinha")
        result = await async_maybe_handle_announcement(self.hass, self.entry, inp)

        self.assertIsNotNone(result)
        self.assertEqual(result.code, "invalid_request")
        self.assertEqual(len(self.hass.services.calls), 0)

    async def test_case_10_and_11_pass_through_queries_and_dialogue(self):
        """Cases 10 & 11: Normal questions and conversation follow normal NLU."""
        for phrase in ("Me fala a temperatura da cozinha", "Fala comigo", "remove leite da lista"):
            inp = make_user_input(phrase)
            result = await async_maybe_handle_announcement(self.hass, self.entry, inp)
            self.assertIsNone(result, f"Expected None for: {phrase}")

    async def test_case_12_mixed_plan_refused(self):
        """Case 12: 'Avisa na sala e apaga a luz' -> refuses mixed plan, zero effects."""
        inp = make_user_input("Avisa na sala e apaga a luz")
        result = await async_maybe_handle_announcement(self.hass, self.entry, inp)

        self.assertIsNotNone(result)
        self.assertEqual(result.code, "invalid_request")
        self.assertEqual(len(self.hass.services.calls), 0)

    async def test_case_14_broadcast_origin_isolation(self):
        """Case 14: Origin B answering 'sim' to request from Origin A does not confirm."""
        inp_a = make_user_input("Anuncia em todos os cômodos que vamos sair", satellite_id="assist_satellite.cozinha", conversation_id="conv_a")
        res_a = await async_maybe_handle_announcement(self.hass, self.entry, inp_a)
        self.assertEqual(res_a.code, "confirmation_required")

        # Origin B says "sim"
        inp_b = make_user_input("sim", satellite_id="assist_satellite.quarto", conversation_id="conv_b")
        res_b = await async_maybe_handle_announcement(self.hass, self.entry, inp_b)

        # Origin B has no active session, so "sim" is not recognized as confirmation
        self.assertIsNone(res_b)  # Falls through, zero broadcast!
        self.assertEqual(len(self.hass.services.calls), 0)

    async def test_case_15_broadcast_fingerprint_mismatch_aborts(self):
        """Case 15: If configuration changes between prompt and confirmation, delivery aborts."""
        inp1 = make_user_input("Anuncia em todos os cômodos que vamos sair")
        await async_maybe_handle_announcement(self.hass, self.entry, inp1)

        # Alter configuration!
        self.entry.options[CONF_ANNOUNCEMENTS_DESTINATIONS]["escritorio"] = [
            {"kind": "media_player", "entity_id": "media_player.escritorio"}
        ]

        # Confirm
        inp2 = make_user_input("sim")
        res2 = await async_maybe_handle_announcement(self.hass, self.entry, inp2)

        self.assertIsNotNone(res2)
        self.assertEqual(res2.code, "denied")
        self.assertEqual(res2.reason, "fingerprint_mismatch")
        self.assertEqual(len(self.hass.services.calls), 0)

    async def test_case_16_permission_denied(self):
        """Case 16: User without permission on target is denied even if service is registered."""
        # Restricted user with no permission on cozinha
        restricted_user = MockUser("user_restricted", allowed_entities={"media_player.sala"})
        self.hass.auth._users["user_restricted"] = restricted_user

        inp = make_user_input("Avisa na cozinha que o almoço está pronto", user_id="user_restricted")
        res = await async_maybe_handle_announcement(self.hass, self.entry, inp)

        self.assertIsNotNone(res)
        self.assertEqual(res.code, "denied")
        self.assertEqual(len(self.hass.services.calls), 0)

    async def test_case_17_destination_unavailable(self):
        """Case 17: Configured destination unavailable refuses without redirecting."""
        self.hass.states = SimpleNamespace(
            get=lambda eid: _State(state="unavailable", attributes={}) if eid == "assist_satellite.cozinha"
            else _State(state="on", attributes={})
        )

        inp = make_user_input("Avisa na cozinha que o almoço está pronto")
        res = await async_maybe_handle_announcement(self.hass, self.entry, inp)

        self.assertIsNotNone(res)
        self.assertEqual(res.code, "unavailable")
        self.assertEqual(len(self.hass.services.calls), 0)

    async def test_case_18_tts_speak_fallback_when_satellite_announce_missing(self):
        """Case 18: If assist_satellite.announce is missing, falls back to tts.speak if configured."""
        self.hass.services.available.remove(("assist_satellite", "announce"))
        # Destination is sala with media_player
        inp = make_user_input("Fala na sala que a reunião começou")
        res = await async_maybe_handle_announcement(self.hass, self.entry, inp)

        self.assertIsNotNone(res)
        self.assertEqual(res.code, "success")
        self.assertEqual(len(self.hass.services.calls), 1)
        self.assertEqual(self.hass.services.calls[0]["domain"], "tts")
        self.assertEqual(self.hass.services.calls[0]["service"], "speak")

    async def test_case_20_cooldown_and_concurrency(self):
        """Case 20: Rapid duplicate requests trigger cooldown."""
        inp1 = make_user_input("Avisa na cozinha que o café está pronto")
        res1 = await async_maybe_handle_announcement(self.hass, self.entry, inp1)
        self.assertEqual(res1.code, "success")

        # Second announcement immediately from same origin triggers cooldown
        inp2 = make_user_input("Avisa no quarto que cheguei")
        res2 = await async_maybe_handle_announcement(self.hass, self.entry, inp2)
        self.assertEqual(res2.code, "unavailable")
        self.assertEqual(res2.reason, "announcement_cooldown")

    async def test_reply_window_workflow(self):
        """Workflow: sender announces to recipient, recipient replies using 'responde que...'"""
        # Step 1: user on satellite.quarto announces to cozinha
        inp1 = make_user_input("Avisa na cozinha que o almoço está pronto", satellite_id="assist_satellite.quarto")
        res1 = await async_maybe_handle_announcement(self.hass, self.entry, inp1)
        self.assertEqual(res1.code, "success")

        # Advance cooldown slightly
        store = get_session_store(self.hass)
        store._cooldowns.clear()

        # Step 2: user on satellite.cozinha replies "responde que já vou"
        inp2 = make_user_input("Responde que já vou", satellite_id="assist_satellite.cozinha")
        res2 = await async_maybe_handle_announcement(self.hass, self.entry, inp2)

        self.assertIsNotNone(res2)
        self.assertEqual(res2.code, "success")
        # Last service call was delivered back to assist_satellite.quarto!
        last_call = self.hass.services.calls[-1]
        self.assertEqual(last_call["target"]["entity_id"], ["assist_satellite.quarto"])
        self.assertEqual(last_call["data"]["message"], "já vou")

    async def test_announcements_disabled_returns_unavailable_no_neural(self):
        """When announcements_enabled=False, returns unavailable without falling back to neural."""
        self.entry.options[CONF_ANNOUNCEMENTS_ENABLED] = False
        inp = make_user_input("Avisa na cozinha que o almoço está pronto")
        res = await async_maybe_handle_announcement(self.hass, self.entry, inp)

        self.assertIsNotNone(res)
        self.assertEqual(res.code, "unavailable")
        self.assertEqual(res.reason, "announcement_disabled")
        self.assertEqual(len(self.hass.services.calls), 0)

    async def test_quiet_hours_denies_announcements(self):
        """During configured quiet hours, announcements are refused."""
        from custom_components.local_nlu.announcement_options import CONF_ANNOUNCEMENTS_QUIET_HOURS
        self.entry.options[CONF_ANNOUNCEMENTS_QUIET_HOURS] = {"start": "00:00", "end": "23:59"}
        inp = make_user_input("Avisa na cozinha que o almoço está pronto")
        res = await async_maybe_handle_announcement(self.hass, self.entry, inp)

        self.assertIsNotNone(res)
        self.assertEqual(res.code, "denied")
        self.assertEqual(res.reason, "quiet_hours")
        self.assertEqual(len(self.hass.services.calls), 0)

    async def test_multi_turn_switches_to_operational_command(self):
        """'Avisa no quarto' -> 'Apaga a luz': router clears session and returns None for NLU."""
        inp1 = make_user_input("Avisa no quarto")
        res1 = await async_maybe_handle_announcement(self.hass, self.entry, inp1)
        self.assertEqual(res1.code, "missing_slot")

        # User gives operational command "Apaga a luz"
        inp2 = make_user_input("Apaga a luz")
        res2 = await async_maybe_handle_announcement(self.hass, self.entry, inp2)

        # Must return None so standard NLU executes "Apaga a luz"!
        self.assertIsNone(res2)
        # Session must be cleared
        store = get_session_store(self.hass)
        self.assertIsNone(store.get_session(SessionOrigin("user_test", "text", "text", "conv_1")))
        self.assertEqual(len(self.hass.services.calls), 0)

    async def test_global_concurrency_busy_limit(self):
        """Exceeding MAX_GLOBAL_CONCURRENT (2) active deliveries returns announcement_busy."""
        store = get_session_store(self.hass)
        # Manually hold 2 slots
        await store.acquire_slot()
        await store.acquire_slot()

        inp = make_user_input("Avisa na cozinha que o almoço está pronto")
        res = await async_maybe_handle_announcement(self.hass, self.entry, inp)

        self.assertIsNotNone(res)
        self.assertEqual(res.code, "unavailable")
        self.assertEqual(res.reason, "announcement_busy")

        await store.release_slot()
        await store.release_slot()


class TestPhase6ConfigFlow(unittest.IsolatedAsyncioTestCase):
    """Test options flow configuration step for announcements."""

    async def test_announcements_options_flow_add_and_save(self):
        from custom_components.local_nlu.config_flow import LocalNluOptionsFlow
        hass = create_mock_hass()
        entry = SimpleNamespace(
            entry_id="test_entry",
            data={},
            options={},
        )
        flow = LocalNluOptionsFlow()
        flow.hass = hass
        flow.config_entry = entry

        # 1. Open announcements form
        res = await flow.async_step_announcements()
        self.assertEqual(res["type"], "form")
        self.assertEqual(res["step_id"], "announcements")

        # 2. Add kitchen satellite mapping
        user_input = {
            CONF_ANNOUNCEMENTS_ENABLED: True,
            CONF_ANNOUNCEMENTS_ALLOW_ALL: True,
            "area_id": "area_cozinha",
            "satellite_id": "assist_satellite.cozinha",
            "save_and_finish": True,
        }
        res2 = await flow.async_step_announcements(user_input)
        self.assertEqual(res2["type"], "create_entry")
        data = res2["data"]
        self.assertTrue(data[CONF_ANNOUNCEMENTS_ENABLED])
        self.assertTrue(data[CONF_ANNOUNCEMENTS_ALLOW_ALL])
        self.assertIn("area_cozinha", data[CONF_ANNOUNCEMENTS_DESTINATIONS])
        self.assertEqual(
            data[CONF_ANNOUNCEMENTS_DESTINATIONS]["area_cozinha"],
            [{"kind": "assist_satellite", "entity_id": "assist_satellite.cozinha"}]
        )
    """End-to-end smoke test through LocalNluConversationEntity.async_process."""

    async def test_entity_async_process_intercepts_announcement(self):
        hass = create_mock_hass()
        options = {
            CONF_ANNOUNCEMENTS_ENABLED: True,
            CONF_ANNOUNCEMENTS_DESTINATIONS: {
                "cozinha": [{"kind": "assist_satellite", "entity_id": "assist_satellite.cozinha"}],
            },
        }

        # Mock runtime that fails if called for announcement!
        mock_runtime = SimpleNamespace(
            async_process=AsyncMock(side_effect=AssertionError("Runtime should NOT be called for announcements!"))
        )

        entry = SimpleNamespace(
            data={},
            options=options,
            runtime_data=mock_runtime,
            entry_id="test_entry",
        )

        entity = LocalNluConversationEntity(entry)
        entity.hass = hass

        user_input = make_user_input("Avisa na cozinha que o almoço está pronto")
        result = await entity.async_process(user_input)

        self.assertIsNotNone(result)
        self.assertIsNotNone(result.response)
        self.assertEqual(result.response.speech["plain"]["speech"], "Aviso enviado.")
        self.assertEqual(len(hass.services.calls), 1)
        mock_runtime.async_process.assert_not_called()
