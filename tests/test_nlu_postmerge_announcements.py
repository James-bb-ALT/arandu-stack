"""Post-merge v11.0.1 regression tests for deterministic announcements (P0)."""

from __future__ import annotations

from datetime import datetime, time, timezone
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.components import conversation
from homeassistant.core import Context

from custom_components.local_nlu.announcement_delivery import (
    check_user_permission,
    is_entity_exposed,
)
from custom_components.local_nlu.announcement_options import (
    CONF_ANNOUNCEMENTS_ALLOW_ALL,
    CONF_ANNOUNCEMENTS_CHIME,
    CONF_ANNOUNCEMENTS_COOLDOWN_S,
    CONF_ANNOUNCEMENTS_DESTINATIONS,
    CONF_ANNOUNCEMENTS_ENABLED,
    CONF_ANNOUNCEMENTS_MAX_TARGETS,
    CONF_ANNOUNCEMENTS_QUIET_HOURS,
    CONF_ANNOUNCEMENTS_TTS_ENTITY,
    is_in_quiet_hours,
    load_announcement_options,
)
from custom_components.local_nlu.announcement_parser import parse_announcement
from custom_components.local_nlu.announcement_router import async_maybe_handle_announcement


class _MockPermissions:
    def __init__(self, allowed: set[str]) -> None:
        self.allowed = set(allowed)

    def check_entity(self, entity_id: str, policy: str) -> bool:
        return entity_id in self.allowed and policy in (POLICY_READ, POLICY_CONTROL)


class _MockUser:
    def __init__(self, user_id: str, allowed: set[str], *, is_active: bool = True, has_perms: bool = True) -> None:
        self.id = user_id
        self.is_active = is_active
        if has_perms:
            self.permissions = _MockPermissions(allowed)


class _MockServices:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.available: set[tuple[str, str]] = {
            ("assist_satellite", "announce"),
            ("tts", "speak"),
        }
        self.fail_on_target: str | None = None

    def has_service(self, domain: str, service: str) -> bool:
        return (domain, service) in self.available

    async def async_call(self, domain: str, service: str, data: dict, blocking: bool = False, context=None, target=None):
        t_list = (target or {}).get("entity_id", [])
        if self.fail_on_target and self.fail_on_target in t_list:
            raise RuntimeError(f"Simulated failure for {self.fail_on_target}")
        self.calls.append({"domain": domain, "service": service, "data": dict(data), "target": target})


def _make_hass(exposed: set[str] | None = None):
    if exposed is None:
        exposed = {
            "assist_satellite.cozinha",
            "assist_satellite.quarto",
            "assist_satellite.escritorio",
            "media_player.sala",
            "tts.piper",
        }
    hass = SimpleNamespace()
    hass.data = {}
    hass.exposed = set(exposed)
    hass.config = SimpleNamespace(time_zone="America/Sao_Paulo")
    hass.states = SimpleNamespace(
        get=lambda eid: SimpleNamespace(
            state="idle" if eid.startswith("media_player") else "on",
            attributes={"friendly_name": eid},
        )
        if eid in hass.exposed
        else None
    )
    hass.entity_registry = SimpleNamespace(entities={})
    hass.area_registry = SimpleNamespace(async_list_areas=lambda: [])
    hass.services = _MockServices()
    all_allowed = set(exposed) | {"assist_satellite.cozinha", "assist_satellite.quarto", "media_player.sala"}
    user = _MockUser("user_1", all_allowed)
    hass.auth = SimpleNamespace(async_get_user=lambda uid: _async_val(user if uid == "user_1" else None))
    return hass


async def _async_val(v):
    return v


def _make_input(text: str, user_id: str = "user_1", satellite_id: str | None = None, conversation_id: str = "conv_1"):
    return conversation.ConversationInput(
        text=text,
        context=Context(user_id=user_id),
        conversation_id=conversation_id,
        device_id=None,
        language="pt-BR",
        satellite_id=satellite_id,
    )


class TestPostmergeAnnouncements(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.hass = _make_hass()
        self.options = {
            CONF_ANNOUNCEMENTS_ENABLED: True,
            CONF_ANNOUNCEMENTS_ALLOW_ALL: True,
            CONF_ANNOUNCEMENTS_TTS_ENTITY: "tts.piper",
            CONF_ANNOUNCEMENTS_CHIME: True,
            CONF_ANNOUNCEMENTS_MAX_TARGETS: 6,
            CONF_ANNOUNCEMENTS_COOLDOWN_S: 3,
            CONF_ANNOUNCEMENTS_DESTINATIONS: {
                "cozinha": [{"kind": "assist_satellite", "entity_id": "assist_satellite.cozinha"}],
                "quarto": [{"kind": "assist_satellite", "entity_id": "assist_satellite.quarto"}],
                "sala": [{"kind": "media_player", "entity_id": "media_player.sala"}],
            },
        }
        self.entry = SimpleNamespace(data={}, options=dict(self.options), entry_id="entry_1")

    async def test_two_stage_broadcast_requires_confirmation(self) -> None:
        r1 = await async_maybe_handle_announcement(self.hass, self.entry, _make_input("avisa na casa toda"))
        self.assertEqual(r1.code, "missing_slot")
        self.assertEqual(len(self.hass.services.calls), 0)

        r2 = await async_maybe_handle_announcement(self.hass, self.entry, _make_input("O jantar está pronto"))
        self.assertEqual(r2.code, "confirmation_required")
        self.assertEqual(len(self.hass.services.calls), 0)

        r3 = await async_maybe_handle_announcement(self.hass, self.entry, _make_input("sim"))
        self.assertEqual(r3.code, "success")
        self.assertEqual(len(self.hass.services.calls), 3)
        self.assertEqual(r3.reason, "accepted_by_ha")

    async def test_two_stage_broadcast_denied_when_allow_all_false(self) -> None:
        self.entry.options[CONF_ANNOUNCEMENTS_ALLOW_ALL] = False
        r1 = await async_maybe_handle_announcement(self.hass, self.entry, _make_input("avisa na casa toda"))
        self.assertEqual(r1.code, "denied")
        self.assertEqual(len(self.hass.services.calls), 0)

    async def test_config_shift_between_prompt_and_confirm_rejects(self) -> None:
        await async_maybe_handle_announcement(self.hass, self.entry, _make_input("avisa na casa toda que cheguei"))
        self.assertEqual(len(self.hass.services.calls), 0)

        # Shift TTS entity or destinations or allow_all
        self.entry.options[CONF_ANNOUNCEMENTS_TTS_ENTITY] = "tts.other"
        r_confirm = await async_maybe_handle_announcement(self.hass, self.entry, _make_input("sim"))
        self.assertEqual(r_confirm.code, "denied")
        self.assertEqual(r_confirm.reason, "fingerprint_mismatch")
        self.assertEqual(len(self.hass.services.calls), 0)

        # Replay of sim must not succeed
        r_replay = await async_maybe_handle_announcement(self.hass, self.entry, _make_input("sim"))
        self.assertIsNone(r_replay)
        self.assertEqual(len(self.hass.services.calls), 0)

    async def test_second_turn_invalid_messages_rejected_without_neural_fallback(self) -> None:
        for bad_msg in ("{{ states('light.sala') }}", "<speak>oi</speak>", "https://evil.test", "oi\x01", "x" * 281):
            await async_maybe_handle_announcement(self.hass, self.entry, _make_input("avisa na cozinha"))
            res = await async_maybe_handle_announcement(self.hass, self.entry, _make_input(bad_msg))
            self.assertIsNotNone(res)
            self.assertEqual(res.code, "invalid_request")
            self.assertEqual(len(self.hass.services.calls), 0)

    def test_fail_closed_exposure_and_permissions(self) -> None:
        with patch.dict(sys.modules, {"homeassistant.components.homeassistant": SimpleNamespace()}):
            self.assertFalse(is_entity_exposed(self.hass, "assist_satellite.cozinha"))

        def _boom(*_a, **_kw):
            raise RuntimeError("exposure api down")

        with patch("homeassistant.components.homeassistant.exposed_entities.async_should_expose", side_effect=_boom):
            self.assertFalse(is_entity_exposed(self.hass, "assist_satellite.cozinha"))

        user_no_perms = _MockUser("u_noperm", set(), has_perms=False)
        self.assertFalse(check_user_permission(user_no_perms, "assist_satellite.cozinha"))
        user_inactive = _MockUser("u_inact", {"assist_satellite.cozinha"}, is_active=False)
        self.assertFalse(check_user_permission(user_inactive, "assist_satellite.cozinha"))

    async def test_revocation_between_targets_stops_and_reports_partial(self) -> None:
        await async_maybe_handle_announcement(self.hass, self.entry, _make_input("avisa na casa toda que cheguei"))

        # Revoke permission on quarto during delivery of cozinha
        orig_call = self.hass.services.async_call
        user = await self.hass.auth.async_get_user("user_1")

        async def _call_and_revoke(domain, service, data, blocking=False, context=None, target=None):
            res = await orig_call(domain, service, data, blocking=blocking, context=context, target=target)
            if "assist_satellite.cozinha" in (target or {}).get("entity_id", []):
                user.permissions.allowed.discard("assist_satellite.quarto")
            return res

        self.hass.services.async_call = _call_and_revoke
        res = await async_maybe_handle_announcement(self.hass, self.entry, _make_input("sim"))
        self.assertEqual(res.code, "unavailable")
        self.assertEqual(res.operation_count, 1)
        self.assertEqual(len(self.hass.services.calls), 1)
        self.assertIn("apenas em 1 de 3", res.response_text)

    async def test_max_targets_exceeded_refuses_without_truncation(self) -> None:
        self.entry.options[CONF_ANNOUNCEMENTS_MAX_TARGETS] = 2
        res = await async_maybe_handle_announcement(self.hass, self.entry, _make_input("avisa na casa toda que cheguei"))
        self.assertEqual(res.code, "denied")
        self.assertEqual(res.reason, "too_many_targets")
        self.assertEqual(len(self.hass.services.calls), 0)

    async def test_ambiguous_and_substring_destinations_refused(self) -> None:
        # Substring must not match
        res_sub = await async_maybe_handle_announcement(self.hass, self.entry, _make_input("avisa na coz que cheguei"))
        self.assertEqual(res_sub.code, "unresolved_target")
        self.assertEqual(len(self.hass.services.calls), 0)

        # Two devices in same area without disambiguation
        self.entry.options[CONF_ANNOUNCEMENTS_DESTINATIONS]["cozinha"] = [
            {"kind": "assist_satellite", "entity_id": "assist_satellite.cozinha"},
            {"kind": "media_player", "entity_id": "media_player.sala"},
        ]
        res_amb = await async_maybe_handle_announcement(self.hass, self.entry, _make_input("avisa na cozinha que cheguei"))
        self.assertEqual(res_amb.code, "unresolved_target")
        self.assertEqual(res_amb.reason, "ambiguous_destination")
        self.assertEqual(len(self.hass.services.calls), 0)

    async def test_satellite_fallback_never_passes_satellite_to_tts_speak(self) -> None:
        self.hass.services.available.discard(("assist_satellite", "announce"))
        res = await async_maybe_handle_announcement(self.hass, self.entry, _make_input("avisa na cozinha que cheguei"))
        self.assertEqual(res.code, "unavailable")
        self.assertIn("não suporta avisos diretos", res.response_text)
        self.assertEqual(len(self.hass.services.calls), 0)

    def test_parser_word_boundary_on_que_inside_destination(self) -> None:
        intent = parse_announcement("avisa no bosque que chegaram")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.destination, "bosque")
        self.assertEqual(intent.message, "chegaram")

        intent2 = parse_announcement("avisa na brinquedoteca que o almoço chegou")
        self.assertIsNotNone(intent2)
        self.assertEqual(intent2.destination, "brinquedoteca")
        self.assertEqual(intent2.message, "o almoço chegou")

    def test_quiet_hours_overnight_with_ha_timezone(self) -> None:
        opts = load_announcement_options({
            CONF_ANNOUNCEMENTS_ENABLED: True,
            CONF_ANNOUNCEMENTS_QUIET_HOURS: {"start": "22:00", "end": "07:00"},
        })
        self.assertTrue(is_in_quiet_hours(opts, current_time=time(23, 30), hass=self.hass))
        self.assertTrue(is_in_quiet_hours(opts, current_time=time(6, 59), hass=self.hass))
        self.assertFalse(is_in_quiet_hours(opts, current_time=time(12, 0), hass=self.hass))

    async def test_failed_delivery_does_not_record_dedup(self) -> None:
        self.hass.services.fail_on_target = "assist_satellite.cozinha"
        r1 = await async_maybe_handle_announcement(self.hass, self.entry, _make_input("avisa na cozinha que cheguei"))
        self.assertEqual(r1.code, "unavailable")

        self.hass.services.fail_on_target = None
        r2 = await async_maybe_handle_announcement(self.hass, self.entry, _make_input("avisa na cozinha que cheguei"))
        self.assertEqual(r2.code, "success")
        self.assertEqual(len(self.hass.services.calls), 1)
