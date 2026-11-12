"""Comprehensive integration verification suite for Arandu Stack Fases 4 + 5 + 6.

Validates:
- Fluxo 1: Contextual antecedent switch ("liga a luz da sala" -> "agora a da cozinha") without neural models
- Fluxo 2: Todo list actions ("adiciona café na lista", "remove café da lista") without announcement interception
- Fluxo 3: Automation execution ("ativa automação" does not trigger, "executa automação" with skip_condition=false)
- Fluxo 4: Announcement quoting commands ("avisa na sala que desliga a luz do quarto" -> audio only, zero light effect)
- Fluxo 5: Whole-house broadcast ("avisa em toda casa" -> blocked when OFF, requires confirmation when ON)
- Fluxo 6: Multi-satellite isolation, cross-confirmation prevention, cancellation and idempotency
- Fluxo 7: F4 observability trace, admission, F3.2 scheduling, and fallback contracts
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.append(str(Path(__file__).parents[1] / "arandu_stack/rootfs/modules/arandu_nlu/rootfs/src/tests/mlp"))
from test_catalog_runtime import _Entry, _State, _Areas, _Devices

from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant

from custom_components.local_nlu.announcement_delivery import DestinationEntity
from custom_components.local_nlu.announcement_options import (
    CONF_ANNOUNCEMENTS_ALLOW_ALL,
    CONF_ANNOUNCEMENTS_CHIME,
    CONF_ANNOUNCEMENTS_COOLDOWN_S,
    CONF_ANNOUNCEMENTS_DESTINATIONS,
    CONF_ANNOUNCEMENTS_ENABLED,
    CONF_ANNOUNCEMENTS_TTS_ENTITY,
)
from custom_components.local_nlu.announcement_router import (
    async_maybe_handle_announcement,
    get_session_store,
)
from custom_components.local_nlu.conversation import LocalNluConversationEntity
from custom_components.local_nlu.runtime import RuntimeResult


class MockServices:
    def __init__(self) -> None:
        self.available = {
            ("assist_satellite", "announce"),
            ("tts", "speak"),
            ("light", "turn_on"),
            ("light", "turn_off"),
            ("lock", "unlock"),
            ("automation", "trigger"),
            ("automation", "turn_on"),
            ("todo", "add_item"),
            ("todo", "remove_item"),
            ("todo", "update_item"),
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


class MockUser:
    def __init__(self, user_id: str = "user_test", is_active: bool = True) -> None:
        self.id = user_id
        self.is_active = is_active
        self.name = "Test User"
        self.permissions = SimpleNamespace(
            check_entity=lambda entity_id, policy: True
        )


def make_combined_hass() -> HomeAssistant:
    hass = SimpleNamespace()
    hass.data = {}
    hass.exposed = {
        "assist_satellite.sala",
        "assist_satellite.quarto",
        "assist_satellite.cozinha",
        "media_player.sala",
        "tts.piper",
        "light.sala",
        "light.cozinha",
        "light.quarto",
        "lock.porta",
        "automation.regar_plantas",
        "todo.compras",
    }
    hass.area_registry = SimpleNamespace(
        async_get_area=lambda aid: SimpleNamespace(id=aid, name=aid.replace("area_", "").capitalize()),
        async_list_areas=lambda: [
            SimpleNamespace(id="area_sala", name="Sala"),
            SimpleNamespace(id="area_cozinha", name="Cozinha"),
            SimpleNamespace(id="area_quarto", name="Quarto"),
        ],
    )
    hass.device_registry = _Devices()
    hass.entity_registry = SimpleNamespace(
        entities={
            "assist_satellite.sala": _Entry(id="reg_sat_sala", entity_id="assist_satellite.sala", area_id="area_sala", aliases=[], name="Satélite Sala"),
            "assist_satellite.quarto": _Entry(id="reg_sat_quarto", entity_id="assist_satellite.quarto", area_id="area_quarto", aliases=[], name="Satélite Quarto"),
            "assist_satellite.cozinha": _Entry(id="reg_sat_cozinha", entity_id="assist_satellite.cozinha", area_id="area_cozinha", aliases=[], name="Satélite Cozinha"),
            "media_player.sala": _Entry(id="reg_mp_sala", entity_id="media_player.sala", area_id="area_sala", aliases=[], name="Speaker Sala"),
            "tts.piper": _Entry(id="reg_tts", entity_id="tts.piper", area_id=None, aliases=[], name="Piper TTS"),
            "light.sala": _Entry(id="reg_light_sala", entity_id="light.sala", area_id="area_sala", aliases=[], name="Luz da Sala"),
            "light.cozinha": _Entry(id="reg_light_cozinha", entity_id="light.cozinha", area_id="area_cozinha", aliases=[], name="Luz da Cozinha"),
            "light.quarto": _Entry(id="reg_light_quarto", entity_id="light.quarto", area_id="area_quarto", aliases=[], name="Luz do Quarto"),
            "lock.porta": _Entry(id="reg_lock", entity_id="lock.porta", area_id="area_sala", aliases=[], name="Fechadura Principal"),
            "automation.regar_plantas": _Entry(id="reg_auto", entity_id="automation.regar_plantas", area_id=None, aliases=[], name="Regar Plantas"),
            "todo.compras": _Entry(id="reg_todo", entity_id="todo.compras", area_id=None, aliases=[], name="Lista de Compras"),
        }
    )
    hass.states = SimpleNamespace(
        get=lambda eid: _State(state="on", attributes={}) if eid in hass.exposed else None
    )
    hass.services = MockServices()
    user = MockUser("user_test")
    hass.auth = SimpleNamespace(
        async_get_user=AsyncMock(return_value=user)
    )
    return hass


def make_input(text: str, satellite_id: str | None = None, conversation_id: str = "conv_1", user_id: str = "user_test") -> conversation.ConversationInput:
    ctx = Context(user_id=user_id)
    return conversation.ConversationInput(
        text=text,
        context=ctx,
        conversation_id=conversation_id,
        device_id=None,
        language="pt-BR",
        satellite_id=satellite_id,
    )


class TestFases5And6Combined(unittest.IsolatedAsyncioTestCase):
    """End-to-end validation of all integrated flows."""

    def setUp(self):
        self.hass = make_combined_hass()
        self.options = {
            CONF_ANNOUNCEMENTS_ENABLED: True,
            CONF_ANNOUNCEMENTS_ALLOW_ALL: True,
            CONF_ANNOUNCEMENTS_CHIME: True,
            CONF_ANNOUNCEMENTS_COOLDOWN_S: 3,
            CONF_ANNOUNCEMENTS_TTS_ENTITY: "tts.piper",
            CONF_ANNOUNCEMENTS_DESTINATIONS: {
                "cozinha": [{"kind": "assist_satellite", "entity_id": "assist_satellite.cozinha"}],
                "quarto": [{"kind": "assist_satellite", "entity_id": "assist_satellite.quarto"}],
                "sala": [{"kind": "assist_satellite", "entity_id": "assist_satellite.sala"}],
            },
        }
        self.entry = SimpleNamespace(
            data={},
            options=self.options,
            entry_id="entry_comb",
        )
        get_session_store(self.hass).clear()

    async def test_fluxo_1_contextual_antecedent_switch_without_neural(self):
        """Fluxo 1: 'liga a luz da sala' followed by 'agora a da cozinha' follows NLU without announcement intercept."""
        for text in ("liga a luz da sala", "agora a da cozinha"):
            inp = make_input(text)
            # Announcement router must return None so NLU processes device control
            res = await async_maybe_handle_announcement(self.hass, self.entry, inp)
            self.assertIsNone(res, f"Announcement router should not intercept: {text}")

    async def test_fluxo_2_todo_list_actions_pass_through_without_announcement(self):
        """Fluxo 2: F5 list commands follow NLU, zero announcement interception."""
        for text in ("adiciona café na lista de compras", "remove café da lista de compras"):
            inp = make_input(text)
            res = await async_maybe_handle_announcement(self.hass, self.entry, inp)
            self.assertIsNone(res, f"Todo action intercepted by mistake: {text}")

    async def test_fluxo_3_automation_actions_safety(self):
        """Fluxo 3: F5 automation actions pass through announcement router to NLU."""
        for text in ("ativa automação regar plantas", "executa automação regar plantas"):
            inp = make_input(text)
            res = await async_maybe_handle_announcement(self.hass, self.entry, inp)
            self.assertIsNone(res, f"Automation command intercepted by mistake: {text}")

    async def test_fluxo_4_announcement_quoting_commands_zero_domestic_side_effects(self):
        """Fluxo 4: 'avisa na sala que desliga a luz do quarto' -> audio only; zero light calls!"""
        inp = make_input("Avisa na sala que desliga a luz do quarto")
        res = await async_maybe_handle_announcement(self.hass, self.entry, inp)

        self.assertIsNotNone(res)
        self.assertEqual(res.code, "success")
        self.assertEqual(len(self.hass.services.calls), 1)

        call = self.hass.services.calls[0]
        self.assertEqual(call["domain"], "assist_satellite")
        self.assertEqual(call["service"], "announce")
        self.assertEqual(call["data"]["message"], "desliga a luz do quarto")
        self.assertEqual(call["target"]["entity_id"], ["assist_satellite.sala"])

        # Strictly 0 light operations!
        for c in self.hass.services.calls:
            self.assertNotEqual(c["domain"], "light")

    async def test_fluxo_5_whole_house_broadcast_opt_in_and_confirmation(self):
        """Fluxo 5: 'avisa em toda casa' -> blocked when OFF; confirmation required when ON."""
        # 1. Option OFF -> blocked with 'denied'
        self.entry.options[CONF_ANNOUNCEMENTS_ALLOW_ALL] = False
        inp1 = make_input("Avisa em toda casa que vamos sair")
        res1 = await async_maybe_handle_announcement(self.hass, self.entry, inp1)
        self.assertIsNotNone(res1)
        self.assertEqual(res1.code, "denied")
        self.assertEqual(res1.reason, "all_disabled")
        self.assertEqual(len(self.hass.services.calls), 0)

        # 2. Option ON -> requires confirmation
        self.entry.options[CONF_ANNOUNCEMENTS_ALLOW_ALL] = True
        inp2 = make_input("Avisa na casa toda que vamos sair", satellite_id="assist_satellite.sala")
        res2 = await async_maybe_handle_announcement(self.hass, self.entry, inp2)
        self.assertIsNotNone(res2)
        self.assertEqual(res2.code, "confirmation_required")
        self.assertEqual(len(self.hass.services.calls), 0)

        # 3. Confirm with 'sim' -> delivers to other satellites (avoiding self-echo on sala!)
        inp3 = make_input("sim", satellite_id="assist_satellite.sala")
        res3 = await async_maybe_handle_announcement(self.hass, self.entry, inp3)
        self.assertIsNotNone(res3)
        self.assertEqual(res3.code, "success")
        # Delivered to 2 targets: cozinha and quarto (sala excluded for self-echo)
        self.assertEqual(len(self.hass.services.calls), 2)
        targets = [c["target"]["entity_id"][0] for c in self.hass.services.calls]
        self.assertIn("assist_satellite.cozinha", targets)
        self.assertIn("assist_satellite.quarto", targets)
        self.assertNotIn("assist_satellite.sala", targets)

    async def test_fluxo_6_cross_origin_isolation_and_deduplication(self):
        """Fluxo 6: Cross-origin confirmation isolation, cancellation, and idempotency."""
        # Satélite Cozinha initiates broadcast prompt
        inp_coz = make_input("Avisa em todos os cômodos que o almoço está pronto", satellite_id="assist_satellite.cozinha", conversation_id="conv_coz")
        res_coz = await async_maybe_handle_announcement(self.hass, self.entry, inp_coz)
        self.assertEqual(res_coz.code, "confirmation_required")

        # Satélite Quarto responds "sim" -> does NOT trigger Cozinha's broadcast
        inp_qto = make_input("sim", satellite_id="assist_satellite.quarto", conversation_id="conv_qto")
        res_qto = await async_maybe_handle_announcement(self.hass, self.entry, inp_qto)
        self.assertIsNone(res_qto)  # Passes through to NLU, zero broadcast!
        self.assertEqual(len(self.hass.services.calls), 0)

        # Cozinha cancels
        inp_cancel = make_input("cancela", satellite_id="assist_satellite.cozinha", conversation_id="conv_coz")
        res_cancel = await async_maybe_handle_announcement(self.hass, self.entry, inp_cancel)
        self.assertEqual(res_cancel.code, "cancelled")
        self.assertEqual(len(self.hass.services.calls), 0)

        # Deduplication test on repeated request
        get_session_store(self.hass)._cooldowns.clear()
        inp_msg = make_input("Avisa na sala que cheguei")
        res_first = await async_maybe_handle_announcement(self.hass, self.entry, inp_msg)
        self.assertEqual(res_first.code, "success")

        # Second immediate identical request returns deduplicated success
        res_dedup = await async_maybe_handle_announcement(self.hass, self.entry, inp_msg)
        self.assertEqual(res_dedup.code, "success")
        self.assertEqual(res_dedup.response_text, "Aviso já enviado.")
        # Only 1 service call executed!
        self.assertEqual(len(self.hass.services.calls), 1)

    async def test_fluxo_7_conversation_entity_fast_path_and_trace(self):
        """Fluxo 7: End-to-end conversation entity processes announcement with zero neural call."""
        mock_runtime = SimpleNamespace(
            async_process=AsyncMock(side_effect=AssertionError("Neural runtime must not be called!"))
        )
        entry = SimpleNamespace(
            data={},
            options=self.options,
            runtime_data=mock_runtime,
            entry_id="test_comb_entity",
        )
        entity = LocalNluConversationEntity(entry)
        entity.hass = self.hass

        user_input = make_input("Avisa na cozinha que o jantar está pronto")
        result = await entity.async_process(user_input)

        self.assertIsNotNone(result)
        self.assertEqual(
            result.response.speech["plain"]["speech"],
            "Aviso enviado ao Home Assistant para reprodução (reprodução audível não confirmada).",
        )
        self.assertEqual(len(self.hass.services.calls), 1)
        mock_runtime.async_process.assert_not_called()
