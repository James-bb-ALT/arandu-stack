"""Post-merge v11.0.1 regression tests for cross-phase dialogue invalidation (P0)."""

from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.components import conversation
from homeassistant.core import Context

from custom_components.local_nlu.announcement_options import (
    CONF_ANNOUNCEMENTS_ALLOW_ALL,
    CONF_ANNOUNCEMENTS_DESTINATIONS,
    CONF_ANNOUNCEMENTS_ENABLED,
)
from custom_components.local_nlu.announcement_router import (
    async_maybe_handle_announcement,
    clear_session_store,
    get_session_store,
)
from custom_components.local_nlu.conversation import LocalNluConversationEntity
from custom_components.local_nlu.dialogue_coordinator import (
    DialogueScope,
    invalidate_announcement_state,
    invalidate_operational_state,
)


class _MockPerms:
    def check_entity(self, _eid: str, policy: str) -> bool:
        return policy in (POLICY_READ, POLICY_CONTROL)


def _make_hass():
    hass = SimpleNamespace()
    hass.data = {}
    hass.exposed = {"assist_satellite.cozinha", "assist_satellite.quarto", "light.sala", "automation.bom_dia"}
    hass.config = SimpleNamespace(time_zone="America/Sao_Paulo")
    hass.states = SimpleNamespace(
        get=lambda eid: SimpleNamespace(state="on", attributes={"friendly_name": eid})
        if eid in hass.exposed
        else None
    )
    hass.entity_registry = SimpleNamespace(entities={})
    hass.area_registry = SimpleNamespace(async_list_areas=lambda: [])
    calls: list[dict] = []

    async def _async_call(domain, service, data, blocking=False, context=None, target=None):
        calls.append({"domain": domain, "service": service, "data": dict(data), "target": target})

    hass.services = SimpleNamespace(
        calls=calls,
        has_service=lambda d, s: (d, s) in {("assist_satellite", "announce"), ("automation", "trigger"), ("light", "turn_on")},
        async_call=_async_call,
    )
    users = {
        "user_1": SimpleNamespace(id="user_1", is_active=True, permissions=_MockPerms()),
        "user_2": SimpleNamespace(id="user_2", is_active=True, permissions=_MockPerms()),
    }
    hass.auth = SimpleNamespace(async_get_user=lambda uid: _async_val(users.get(uid)))
    return hass


async def _async_val(v):
    return v


def _make_input(text: str, user_id: str = "user_1", satellite_id: str = "assist_satellite.quarto", conversation_id: str = "conv_1"):
    return conversation.ConversationInput(
        text=text,
        context=Context(user_id=user_id),
        conversation_id=conversation_id,
        device_id=None,
        language="pt-BR",
        satellite_id=satellite_id,
    )


class TestPostmergeCrossSessions(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.hass = _make_hass()
        self.options = {
            CONF_ANNOUNCEMENTS_ENABLED: True,
            CONF_ANNOUNCEMENTS_ALLOW_ALL: True,
            CONF_ANNOUNCEMENTS_DESTINATIONS: {
                "cozinha": [{"kind": "assist_satellite", "entity_id": "assist_satellite.cozinha"}],
                "quarto": [{"kind": "assist_satellite", "entity_id": "assist_satellite.quarto"}],
            },
        }

    async def test_pending_automation_invalidated_by_announcement_so_sim_does_not_trigger(self) -> None:
        pending_confirmations = {("user_1", "assist_satellite.quarto", "conv_1"): SimpleNamespace(
            pending={"x": 1}, selection=None, confirmation=("op_auto",), candidates=(), todo_confirmation={}, dialogue=None, temporal_clarification=None
        )}

        class _FakeContextual:
            def __init__(self):
                self.sessions = pending_confirmations

            def invalidate_scope(self, user_id: str, origin_id: str, conversation_id: str | None) -> None:
                sess = self.sessions.get((user_id, origin_id, conversation_id or ""))
                if sess:
                    sess.confirmation = ()
                    sess.pending = None

        contextual = _FakeContextual()

        async def _runtime_process(inp):
            sess = contextual.sessions.get((inp.context.user_id, inp.satellite_id, inp.conversation_id))
            if inp.text.strip().lower() == "sim" and sess and sess.confirmation:
                await self.hass.services.async_call("automation", "trigger", {"skip_condition": False}, target={"entity_id": ["automation.bom_dia"]})
                return SimpleNamespace(code="success", response_text="Automação executada.", conversation_id=inp.conversation_id, continue_conversation=False, reason=None)
            return SimpleNamespace(code="stale", response_text="Não há confirmação pendente.", conversation_id=inp.conversation_id, continue_conversation=False, reason="stale")

        runtime = SimpleNamespace(
            _contextual=contextual,
            _pending={"conv_1": object()},
            invalidate_pending=lambda cid: runtime._pending.pop(cid, None),
            async_process=AsyncMock(side_effect=_runtime_process),
        )
        entry = SimpleNamespace(data={}, options=self.options, runtime_data=runtime, entry_id="e1")
        entity = LocalNluConversationEntity(entry)
        entity.hass = self.hass

        # 1. User starts announcement -> invalidates pending automation confirmation
        res_ann = await entity.async_process(_make_input("avisa na cozinha que cheguei"))
        self.assertEqual(res_ann.response.speech["plain"]["speech"], "Aviso enviado ao Home Assistant para reprodução (reprodução audível não confirmada).")
        self.assertEqual(contextual.sessions[("user_1", "assist_satellite.quarto", "conv_1")].confirmation, ())
        self.assertNotIn("conv_1", runtime._pending)

        # 2. User says "sim" -> must NOT trigger automation
        res_sim = await entity.async_process(_make_input("sim"))
        self.assertEqual(res_sim.response.speech["plain"]["speech"], "Não há confirmação pendente.")
        auto_calls = [c for c in self.hass.services.calls if c["domain"] == "automation"]
        self.assertEqual(len(auto_calls), 0)

    async def test_pending_broadcast_invalidated_by_operational_command_so_sim_does_not_announce(self) -> None:
        runtime = SimpleNamespace(
            _contextual=None,
            _pending={},
            async_process=AsyncMock(return_value=SimpleNamespace(
                code="success", response_text="Luz ligada.", conversation_id="conv_1", continue_conversation=False, reason=None
            )),
        )
        entry = SimpleNamespace(data={}, options=self.options, runtime_data=runtime, entry_id="e1")
        entity = LocalNluConversationEntity(entry)
        entity.hass = self.hass

        # 1. Start broadcast -> confirmation_required
        await entity.async_process(_make_input("avisa na casa toda que cheguei"))
        self.assertEqual(len(self.hass.services.calls), 0)

        # 2. Operational command interrupts broadcast
        res_op = await entity.async_process(_make_input("liga a luz da sala"))
        self.assertEqual(res_op.response.speech["plain"]["speech"], "Luz ligada.")

        # 3. Subsequent "sim" must NOT broadcast audio
        await entity.async_process(_make_input("sim"))
        ann_calls = [c for c in self.hass.services.calls if c["domain"] == "assist_satellite"]
        self.assertEqual(len(ann_calls), 0)

    async def test_two_origins_isolated_and_options_clear(self) -> None:
        entry = SimpleNamespace(data={}, options=self.options, entry_id="e1")
        inp_a = _make_input("avisa na casa toda que cheguei", user_id="user_1", satellite_id="assist_satellite.quarto", conversation_id="c1")
        inp_b = _make_input("avisa na casa toda que sai", user_id="user_2", satellite_id="assist_satellite.cozinha", conversation_id="c2")

        r_a = await async_maybe_handle_announcement(self.hass, entry, inp_a)
        r_b = await async_maybe_handle_announcement(self.hass, entry, inp_b)
        self.assertEqual(r_a.code, "confirmation_required")
        self.assertEqual(r_b.code, "confirmation_required")

        # Cancel origin A only -> origin B remains confirmable
        await async_maybe_handle_announcement(self.hass, entry, _make_input("cancela", user_id="user_1", satellite_id="assist_satellite.quarto", conversation_id="c1"))
        r_b_confirm = await async_maybe_handle_announcement(self.hass, entry, _make_input("sim", user_id="user_2", satellite_id="assist_satellite.cozinha", conversation_id="c2"))
        self.assertEqual(r_b_confirm.code, "success")
        self.assertEqual(len(self.hass.services.calls), 1)

        # Clear store on options update discards any pending session
        await async_maybe_handle_announcement(self.hass, entry, inp_a)
        clear_session_store(self.hass, "e1")
        r_after_clear = await async_maybe_handle_announcement(self.hass, entry, _make_input("sim", user_id="user_1", satellite_id="assist_satellite.quarto", conversation_id="c1"))
        self.assertIsNone(r_after_clear)
