"""FIXTURE_TECNICA Step 7 runtime tests: routing, prohibitions, security."""

from __future__ import annotations

import sys
import types
import unittest
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any


def _install_home_assistant_fakes() -> None:
    homeassistant = types.ModuleType("homeassistant")
    homeassistant.__path__ = []
    const = types.ModuleType("homeassistant.const")
    const.STATE_UNAVAILABLE = "unavailable"
    const.STATE_UNKNOWN = "unknown"

    helpers = types.ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    area_registry = types.ModuleType("homeassistant.helpers.area_registry")
    device_registry = types.ModuleType("homeassistant.helpers.device_registry")
    entity_registry = types.ModuleType("homeassistant.helpers.entity_registry")
    area_registry.async_get = lambda hass: hass.area_registry
    device_registry.async_get = lambda hass: hass.device_registry
    entity_registry.async_get = lambda hass: hass.entity_registry
    helpers.area_registry = area_registry
    helpers.device_registry = device_registry
    helpers.entity_registry = entity_registry

    auth = types.ModuleType("homeassistant.auth")
    auth.__path__ = []
    permissions = types.ModuleType("homeassistant.auth.permissions")
    permissions.__path__ = []
    permission_const = types.ModuleType("homeassistant.auth.permissions.const")
    permission_const.POLICY_CONTROL = "control"
    permission_const.POLICY_READ = "read"

    components = types.ModuleType("homeassistant.components")
    components.__path__ = []
    homeassistant_component = types.ModuleType(
        "homeassistant.components.homeassistant"
    )
    homeassistant_component.__path__ = []
    exposed = types.ModuleType(
        "homeassistant.components.homeassistant.exposed_entities"
    )
    exposed.async_should_expose = (
        lambda hass, assistant, entity_id: (
            assistant == "conversation" and entity_id in hass.exposed
        )
    )
    homeassistant_component.exposed_entities = exposed

    sys.modules.update(
        {
            "homeassistant": homeassistant,
            "homeassistant.const": const,
            "homeassistant.helpers": helpers,
            "homeassistant.helpers.area_registry": area_registry,
            "homeassistant.helpers.device_registry": device_registry,
            "homeassistant.helpers.entity_registry": entity_registry,
            "homeassistant.auth": auth,
            "homeassistant.auth.permissions": permissions,
            "homeassistant.auth.permissions.const": permission_const,
            "homeassistant.components": components,
            "homeassistant.components.homeassistant": homeassistant_component,
            "homeassistant.components.homeassistant.exposed_entities": exposed,
        }
    )


_install_home_assistant_fakes()

from custom_components.local_nlu.catalog import CatalogError  # noqa: E402
from custom_components.local_nlu.runtime import (  # noqa: E402
    SHADOW_COUNTERS,
    LocalNluRuntime,
    _er_allowed_row,
    _looks_like_music,
    _route_v1_first,
)
from custom_components.local_nlu.client import ClientError  # noqa: E402


@dataclass
class _Entry:
    id: str
    entity_id: str
    area_id: str | None = None
    aliases: list[str] = field(default_factory=list)
    name: str | None = None
    original_name_unprefixed: str | None = None
    original_name: str | None = None
    device_id: str | None = None
    disabled_by: str | None = None


@dataclass
class _State:
    state: str
    attributes: dict[str, Any]


class _Registry:
    def __init__(self, entries: list[_Entry]) -> None:
        self.entities = {entry.entity_id: entry for entry in entries}


class _Areas:
    def __init__(self) -> None:
        self.areas = {
            "area_quarto": SimpleNamespace(name="Quarto", aliases=set()),
            "area_sala": SimpleNamespace(name="Sala", aliases=set()),
        }


class _Devices:
    def async_get(self, _: str) -> None:
        return None


class _States:
    def __init__(self, states: dict[str, _State]) -> None:
        self._states = states

    def get(self, entity_id: str) -> _State | None:
        return self._states.get(entity_id)


class _Services:
    def __init__(self, after_call: Any = None) -> None:
        self.available = {
            ("fan", "set_percentage"),
            ("fan", "turn_off"),
            ("fan", "turn_on"),
            ("light", "turn_off"),
            ("light", "turn_on"),
            ("media_player", "media_next_track"),
            ("media_player", "media_pause"),
            ("media_player", "media_play"),
            ("media_player", "media_previous_track"),
            ("media_player", "volume_set"),
            ("music_assistant", "play_media"),
            ("music_assistant", "search"),
            ("switch", "turn_off"),
            ("switch", "turn_on"),
        }
        self.calls: list[dict[str, Any]] = []
        self.after_call = after_call

    def has_service(self, domain: str, service: str) -> bool:
        return (domain, service) in self.available

    async def async_call(
        self, domain: str, service: str, data: dict[str, Any], *,
        blocking: bool, context: Any, target: dict[str, Any],
    ) -> None:
        self.calls.append(
            {
                "blocking": blocking, "context": context, "data": data,
                "domain": domain, "service": service, "target": target,
            }
        )
        if self.after_call is not None:
            self.after_call()


class _Permissions:
    def __init__(self, allowed: set[tuple[str, str]]) -> None:
        self.allowed = allowed

    def check_entity(self, entity_id: str, policy: str) -> bool:
        return (entity_id, policy) in self.allowed


class _User:
    def __init__(self, allowed: set[tuple[str, str]]) -> None:
        self.is_active = True
        self.is_admin = False
        self.permissions = _Permissions(allowed)


class _Auth:
    def __init__(self, user: _User | None) -> None:
        self.user = user

    async def async_get_user(self, _: str) -> _User | None:
        return self.user


class _ClientV2:
    def __init__(
        self,
        v1_response: Any = None,
        v2_response: Any = None,
        mutate: Any = None,
        resolve_map: dict[str, Any] | None = None,
        fail_v2: bool = False,
    ) -> None:
        self.v1_response = v1_response
        self.v2_response = v2_response
        self.mutate = mutate
        self.resolve_map = resolve_map or {}
        self.fail_v2 = fail_v2
        self.v1_calls: list[dict[str, Any]] = []
        self.v2_calls: list[dict[str, Any]] = []
        self.v3_calls: list[dict[str, Any]] = []
        self.resolve_calls: list[dict[str, Any]] = []

    async def async_interpret(self, payload: dict[str, Any]) -> Any:
        self.v1_calls.append(payload)
        if self.mutate is not None:
            self.mutate()
        return self.v1_response

    async def async_interpret_v2(self, payload: dict[str, Any]) -> Any:
        self.v2_calls.append(payload)
        if self.fail_v2:
            raise ClientError("transport")
        if self.mutate is not None:
            self.mutate()
        return self.v2_response

    async def async_interpret_v3(self, payload: dict[str, Any]) -> Any:
        self.v3_calls.append(payload)
        if self.mutate is not None:
            self.mutate()
        if (
            type(self.v2_response) is dict
            and self.v2_response.get("version") == 2
        ):
            converted = dict(self.v2_response)
            converted["version"] = 3
            if converted.get("status") == "ambiguous":
                converted["status"] = "ambiguous_target"
            if converted.get("status") == "no_match":
                converted["status"] = "unsupported_intent"
            return converted
        return self.v2_response

    async def async_resolve(self, payload: dict[str, Any]) -> Any:
        self.resolve_calls.append(payload)
        if payload.get("generation") == "gen-000":
            return {"outcome": "no_match"}
        key = payload.get("mention")
        if key in self.resolve_map:
            return self.resolve_map[key]
        return {"outcome": "no_match"}


def _hass(user: _User | None = None) -> Any:
    entries = [
        _Entry(
            id="reg_light", entity_id="light.sala", area_id="area_sala",
            aliases=["Luz principal"], name="Luz da sala",
            original_name="Luz da sala",
        ),
        _Entry(
            id="reg_quarto", entity_id="light.quarto", area_id="area_quarto",
            name="Luz do quarto", original_name="Luz do quarto",
        ),
        _Entry(
            id="reg_fan", entity_id="fan.ventilador", area_id="area_quarto",
            name="Ventilador", original_name="Ventilador",
        ),
        _Entry(
            id="reg_lamp_sala", entity_id="light.abajur_sala",
            area_id="area_sala", name="Abajur", original_name="Abajur",
            aliases=["Abajur da sala"],
        ),
        _Entry(
            id="reg_lamp_quarto", entity_id="light.abajur_quarto",
            area_id="area_quarto", name="Abajur", original_name="Abajur",
            aliases=["Abajur do quarto"],
        ),
        _Entry(
            id="reg_ma_sala", entity_id="media_player.sala_ma",
            area_id="area_sala", name="Sala MA", original_name="Sala MA",
        ),
    ]
    states = {
        "light.sala": _State("on", {"friendly_name": "Luz da sala"}),
        "light.quarto": _State("on", {"friendly_name": "Luz do quarto"}),
        "fan.ventilador": _State(
            "on", {"friendly_name": "Ventilador", "supported_features": 49}
        ),
        "media_player.sala_ma": _State(
            "idle", {"friendly_name": "Sala", "mass_player_type": "player"}
        ),
        "light.abajur_sala": _State("on", {"friendly_name": "Abajur"}),
        "light.abajur_quarto": _State("on", {"friendly_name": "Abajur"}),
        "media_player.spotify": _State(
            "idle", {"friendly_name": "Spotify"}
        ),
    }
    return SimpleNamespace(
        area_registry=_Areas(),
        auth=_Auth(user),
        device_registry=_Devices(),
        entity_registry=_Registry(entries),
        exposed={entry.entity_id for entry in entries},
        services=_Services(),
        states=_States(states),
    )


def _input(text: str = "Acenda a luz.", conversation_id: str = "conv") -> Any:
    context = SimpleNamespace(user_id="FIXTURE_TECNICA_USER")
    return SimpleNamespace(
        context=context, conversation_id=conversation_id, text=text
    )


def _v2_plan(*operations: Any) -> dict[str, Any]:
    return {"operations": list(operations), "status": "plan", "version": 2}


def _op(action: str, targets: list[str], percentage: Any = None) -> dict[str, Any]:
    operation: dict[str, Any] = {"action": action, "targets": targets}
    if percentage is not None:
        operation["percentage"] = percentage
    return operation


def _runtime(
    hass: Any,
    client: _ClientV2,
    v2: bool = False,
    shadow: bool = False,
) -> tuple[LocalNluRuntime, list[bool], list[bool]]:
    v2_cell = [v2]
    shadow_cell = [shadow]
    runtime = LocalNluRuntime(
        hass, client, lambda: v2_cell[0], lambda: shadow_cell[0]
    )
    return runtime, v2_cell, shadow_cell


class SelectorTests(unittest.TestCase):
    def test_queries_conjunctions_and_unsafe_inputs_stay_v1(self) -> None:
        self.assertTrue(_route_v1_first("Qual é o estado do abajur?"))
        self.assertTrue(_route_v1_first("  COMO está a sala?"))
        self.assertTrue(_route_v1_first("Quanto está o ventilador?"))
        self.assertTrue(_route_v1_first(None))
        self.assertTrue(_route_v1_first(123))

    def test_single_effect_commands_are_v2_eligible(self) -> None:
        self.assertFalse(_route_v1_first("Acenda a luz."))
        self.assertFalse(_route_v1_first("Apague a luz e ligue o ventilador."))
        self.assertFalse(_route_v1_first("Coloque o ventilador em 50 por cento."))
        self.assertFalse(_route_v1_first("Acenda light.luz_sala."))
        self.assertFalse(_route_v1_first(""))

    def test_music_commands_are_detected_for_v3(self) -> None:
        self.assertTrue(_looks_like_music("Toca Queen no Spotify na sala."))
        self.assertTrue(_looks_like_music("Pausa a música."))
        self.assertFalse(_looks_like_music("Apague a luz da sala."))


class RoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_flag_off_never_touches_v2(self) -> None:
        hass = _hass(_User({("light.sala", "control")}))
        client = _ClientV2(
            v1_response={
                "operations": [{"action": "turn_off", "targets": ["reg_light"]}],
                "status": "plan",
                "version": 1,
            },
            v2_response=_v2_plan(_op("turn_off", ["reg_light"])),
        )
        runtime, _, _ = _runtime(hass, client, v2=False)

        result = await runtime.async_process(_input("Acenda a luz."))

        self.assertEqual(result.code, "success")
        self.assertEqual(len(client.v1_calls), 1)
        self.assertEqual(client.v2_calls, [])

    async def test_queries_preroute_but_conjunctions_use_v2(self) -> None:
        hass = _hass(_User({("light.sala", "control")}))
        client = _ClientV2(
            v1_response={
                "operations": [{"action": "turn_off", "targets": ["reg_light"]}],
                "status": "plan",
                "version": 1,
            },
            v2_response=_v2_plan(_op("turn_off", ["reg_light"])),
        )
        runtime, _, _ = _runtime(hass, client, v2=True)

        result = await runtime.async_process(_input("Qual é o estado do abajur?"))
        self.assertEqual(result.code, "success")
        result = await runtime.async_process(
            _input("Apague a luz e ligue o ventilador.")
        )
        self.assertEqual(result.code, "success")

        self.assertEqual(len(client.v1_calls), 1)
        self.assertEqual(len(client.v3_calls), 1)

    async def test_single_effect_uses_v2_exactly_once(self) -> None:
        hass = _hass(_User({("light.sala", "control")}))
        client = _ClientV2(
            v2_response=_v2_plan(_op("turn_off", ["reg_light"])),
        )
        runtime, _, _ = _runtime(hass, client, v2=True)
        user_input = _input("Apague a luz.")

        result = await runtime.async_process(user_input)

        self.assertEqual(result.code, "success")
        self.assertEqual(len(client.v3_calls), 1)
        self.assertEqual(client.v1_calls, [])
        payload = client.v3_calls[0]
        self.assertEqual(
            sorted(payload), ["catalog", "generation", "text"]
        )
        self.assertEqual(
            payload["generation"], payload["catalog"]["generation"]
        )
        self.assertEqual(
            [(c["domain"], c["service"]) for c in hass.services.calls],
            [("light", "turn_off")],
        )
        self.assertTrue(
            all(c["context"] is user_input.context for c in hass.services.calls)
        )


class MusicAssistantRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_music_play_uses_music_assistant_search_and_play_media(self) -> None:
        hass = _hass(_User({("media_player.sala_ma", "control")}))
        client = _ClientV2(
            v2_response={
                "music": {
                    "action": "play",
                    "media_query": "Queen",
                    "provider": "spotify",
                    "player": "media_player.sala_ma",
                },
                "status": "plan",
                "version": 3,
            },
        )
        runtime, _, _ = _runtime(hass, client, v2=True)

        result = await runtime.async_process(_input("Toca Queen no Spotify na sala."))

        self.assertEqual(result.code, "success")
        self.assertEqual(client.v1_calls, [])
        self.assertEqual(client.v2_calls, [])
        self.assertEqual(len(client.v3_calls), 1)
        self.assertEqual(
            [(c["domain"], c["service"]) for c in hass.services.calls],
            [("music_assistant", "search"), ("music_assistant", "play_media")],
        )
        self.assertEqual(hass.services.calls[0]["data"]["provider"], "spotify")
        self.assertEqual(
            hass.services.calls[1]["target"],
            {"entity_id": ["media_player.sala_ma"]},
        )

    async def test_spotify_entity_is_not_accepted_as_music_assistant_player(self) -> None:
        hass = _hass(_User({("media_player.spotify", "control")}))
        client = _ClientV2(
            v2_response={
                "music": {
                    "action": "play",
                    "media_query": "Queen",
                    "provider": "spotify",
                    "player": "media_player.spotify",
                },
                "status": "plan",
                "version": 3,
            },
        )
        runtime, _, _ = _runtime(hass, client, v2=True)

        result = await runtime.async_process(_input("Toca Queen no Spotify."))

        self.assertEqual(result.code, "stale")
        self.assertEqual(hass.services.calls, [])

    async def test_provider_without_content_requests_missing_media_query(self) -> None:
        hass = _hass(_User({("media_player.sala_ma", "control")}))
        client = _ClientV2(
            v2_response={
                "missing_slot": "media_query",
                "provider": "deezer",
                "status": "missing_slot",
                "version": 3,
            },
        )
        runtime, _, _ = _runtime(hass, client, v2=True)

        result = await runtime.async_process(_input("Toca no Deezer."))

        self.assertEqual(result.code, "missing_slot")
        self.assertEqual(result.missing_slot, "media_query")
        self.assertEqual(result.provider, "deezer")
        self.assertEqual(hass.services.calls, [])

        client.v2_response = {
            "music": {
                "action": "play",
                "media_query": "Daft Punk",
                "provider": "deezer",
                "player": "media_player.sala_ma",
            },
            "status": "plan",
            "version": 3,
        }
        followup = await runtime.async_process(_input("Daft Punk"))
        self.assertEqual(followup.code, "success")
        self.assertEqual(
            [(c["domain"], c["service"]) for c in hass.services.calls],
            [("music_assistant", "search"), ("music_assistant", "play_media")],
        )

    async def test_target_clarification_continues_once_and_executes_only_selected_slot(self) -> None:
        hass = _hass(
            _User({
                ("light.abajur_sala", "control"),
                ("light.abajur_quarto", "control"),
            })
        )
        client = _ClientV2(
            v2_response={
                "action": "turn_off",
                "candidates": ["reg_lamp_quarto", "reg_lamp_sala"],
                "domain": "light",
                "missing_slot": "target",
                "status": "missing_slot",
                "version": 3,
            },
        )
        runtime, _, _ = _runtime(hass, client, v2=True)

        first = await runtime.async_process(_input("desliga o abajur", "amb"))
        self.assertEqual(first.code, "missing_slot")
        self.assertEqual(hass.services.calls, [])

        second = await runtime.async_process(_input("o do quarto", "amb"))
        self.assertEqual(second.code, "success")
        self.assertEqual(
            hass.services.calls[0]["target"],
            {"entity_id": ["light.abajur_quarto"]},
        )

        replay = await runtime.async_process(_input("o da sala", "amb"))
        self.assertNotEqual(replay.code, "success")

    async def test_session_mismatch_does_not_consume_pending_target(self) -> None:
        hass = _hass(_User({("light.abajur_sala", "control")}))
        client = _ClientV2(
            v2_response={
                "action": "turn_off",
                "candidates": ["reg_lamp_sala"],
                "domain": "light",
                "missing_slot": "target",
                "status": "missing_slot",
                "version": 3,
            },
        )
        runtime, _, _ = _runtime(hass, client, v2=True)

        first = await runtime.async_process(_input("desliga a luz de inexistente", "a"))
        mismatch = await runtime.async_process(_input("a da sala", "b"))
        valid = await runtime.async_process(_input("a da sala", "a"))

        self.assertEqual(first.code, "missing_slot")
        self.assertNotEqual(mismatch.code, "success")
        self.assertEqual(valid.code, "success")


class ProhibitionTests(unittest.IsolatedAsyncioTestCase):
    async def test_v2_abstention_is_terminal(self) -> None:
        hass = _hass(_User({("light.sala", "control")}))
        for status, expected in (("ambiguous", "ambiguous"), ("no_match", "unsupported_intent")):
            client = _ClientV2(v2_response={"status": status, "version": 2})
            runtime, _, _ = _runtime(hass, client, v2=True)

            result = await runtime.async_process(_input("Acenda a luz."))

            self.assertEqual(result.code, expected)
            self.assertEqual(client.v1_calls, [])
            self.assertEqual(hass.services.calls, [])

    async def test_v2_transport_failure_never_falls_back(self) -> None:
        hass = _hass(_User({("light.sala", "control")}))
        client = _ClientV2(fail_v2=True)
        runtime, _, _ = _runtime(hass, client, v2=True)

        result = await runtime.async_process(_input("Acenda a luz."))

        self.assertEqual(result.code, "unavailable")
        self.assertEqual(client.v1_calls, [])

    async def test_v2_preflight_failure_never_falls_back(self) -> None:
        hass = _hass(_User({("light.sala", "control")}))

        def mutate() -> None:
            del hass.entity_registry.entities["light.sala"]

        client = _ClientV2(
            v2_response=_v2_plan(_op("turn_off", ["reg_light"])),
            mutate=mutate,
        )
        runtime, _, _ = _runtime(hass, client, v2=True)

        result = await runtime.async_process(_input("Acenda a luz."))

        self.assertEqual(result.code, "stale")
        self.assertEqual(client.v1_calls, [])
        self.assertEqual(hass.services.calls, [])

    async def test_denied_is_terminal_without_fallback(self) -> None:
        hass = _hass(_User(set()))
        client = _ClientV2(
            v2_response=_v2_plan(_op("turn_off", ["reg_light"])),
        )
        runtime, _, _ = _runtime(hass, client, v2=True)

        result = await runtime.async_process(_input("Acenda a luz."))

        self.assertEqual(result.code, "denied")
        self.assertEqual(client.v1_calls, [])
        self.assertEqual(hass.services.calls, [])


class SecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_generation_change_blocks_before_first_effect(self) -> None:
        hass = _hass(_User({("light.sala", "control")}))

        def mutate() -> None:
            hass.states._states["light.sala"].attributes[
                "friendly_name"
            ] = "Nome alterado"

        client = _ClientV2(
            v2_response=_v2_plan(_op("turn_off", ["reg_light"])),
            mutate=mutate,
        )
        runtime, _, _ = _runtime(hass, client, v2=True)

        result = await runtime.async_process(_input("Acenda a luz."))

        self.assertEqual(result.code, "stale")
        self.assertEqual(hass.services.calls, [])

    async def test_rebuild_failure_is_stale(self) -> None:
        hass = _hass(_User({("light.sala", "control")}))

        def mutate() -> None:
            hass.area_registry.areas.clear()

        client = _ClientV2(
            v2_response=_v2_plan(_op("turn_off", ["reg_light"])),
            mutate=mutate,
        )
        runtime, _, _ = _runtime(hass, client, v2=True)

        result = await runtime.async_process(_input("Acenda a luz."))

        self.assertEqual(result.code, "stale")
        self.assertEqual(hass.services.calls, [])

    async def test_permission_revoked_before_execution_denies(self) -> None:
        user = _User({("light.sala", "control")})
        hass = _hass(user)

        def mutate() -> None:
            user.permissions.allowed.clear()

        client = _ClientV2(
            v2_response=_v2_plan(_op("turn_off", ["reg_light"])),
            mutate=mutate,
        )
        runtime, _, _ = _runtime(hass, client, v2=True)

        result = await runtime.async_process(_input("Acenda a luz."))

        self.assertEqual(result.code, "denied")
        self.assertEqual(hass.services.calls, [])

    async def test_mid_chain_failure_preserves_count(self) -> None:
        hass = _hass(
            _User({("light.sala", "control"), ("light.quarto", "control")})
        )
        hass.services.after_call = lambda: hass.exposed.discard("light.quarto")
        client = _ClientV2(
            v2_response=_v2_plan(
                _op("turn_off", ["reg_light"]),
                _op("turn_off", ["reg_quarto"]),
            ),
        )
        runtime, _, _ = _runtime(hass, client, v2=True)

        result = await runtime.async_process(_input("Apague a luz."))

        self.assertEqual(result.code, "stale")
        self.assertEqual(result.operation_count, 1)
        self.assertEqual(
            [(c["domain"], c["service"]) for c in hass.services.calls],
            [("light", "turn_off")],
        )

    async def test_adapter_rejects_malformed_snapshot_rows(self) -> None:
        good = {
            "registry_id": "reg_x",
            "entity_id": "light.x",
            "display_name": "X",
            "domain": "light",
            "capabilities": ["turn_on"],
        }
        mapped = _er_allowed_row(good)
        self.assertEqual(
            mapped,
            {
                "actions": ["turn_on"],
                "entity_id": "light.x",
                "names": ["X"],
                "registry_id": "reg_x",
            },
        )
        bad_rows = [
            dict(good, display_name=""),
            dict(good, domain="climate"),
            dict(good, capabilities=[]),
            dict(good, capabilities=["set_fan_percentage"]),
            dict(good, registry_id="bad id!"),
            {k: v for k, v in good.items() if k != "entity_id"},
        ]
        for row in bad_rows:
            with self.subTest(row=row), self.assertRaises(CatalogError):
                _er_allowed_row(row)


class KillSwitchTests(unittest.IsolatedAsyncioTestCase):
    async def test_mid_flight_disable_completes_terminally(self) -> None:
        hass = _hass(_User({("light.sala", "control")}))
        runtime, v2_cell, _ = _runtime(hass, None, v2=True)
        client = _ClientV2(
            v2_response=_v2_plan(_op("turn_off", ["reg_light"])),
        )

        def mutate() -> None:
            v2_cell[0] = False

        client.mutate = mutate
        runtime._client = client

        result = await runtime.async_process(_input("Acenda a luz."))

        self.assertEqual(result.code, "success")
        self.assertEqual(len(client.v3_calls), 1)
        self.assertEqual(client.v1_calls, [])

        followup = await runtime.async_process(_input("Acenda a luz."))
        _ = followup
        self.assertEqual(len(client.v3_calls), 1)
        self.assertEqual(len(client.v1_calls), 1)

    async def test_never_executes_both_paths(self) -> None:
        hass = _hass(_User({("light.sala", "control")}))
        client = _ClientV2(
            v1_response={
                "operations": [{"action": "turn_on", "targets": ["reg_light"]}],
                "status": "plan",
                "version": 1,
            },
            v2_response=_v2_plan(_op("turn_off", ["reg_light"])),
        )
        runtime, _, _ = _runtime(hass, client, v2=True)

        await runtime.async_process(_input("Acenda a luz."))
        await runtime.async_process(_input("Qual é o estado?"))

        self.assertEqual(len(client.v3_calls), 1)
        self.assertEqual(len(client.v1_calls), 1)
        self.assertEqual(
            [(c["domain"], c["service"]) for c in hass.services.calls],
            [("light", "turn_off"), ("light", "turn_on")],
        )


class ShadowTests(unittest.IsolatedAsyncioTestCase):
    def _resolve_map(self) -> dict[str, Any]:
        return {
            "light.sala": {
                "outcome": "resolved",
                "registry_id": "reg_light",
                "evidence": "external_entity_id",
            },
            "light.quarto": {
                "outcome": "resolved",
                "registry_id": "reg_quarto",
                "evidence": "external_entity_id",
            },
            "fan.ventilador": {
                "outcome": "resolved",
                "registry_id": "reg_fan",
                "evidence": "external_entity_id",
            },
        }

    async def test_shadow_observes_with_fixed_aggregate_schema(self) -> None:
        hass = _hass(_User({("light.sala", "control")}))
        client = _ClientV2(
            v1_response={
                "operations": [{"action": "turn_off", "targets": ["reg_light"]}],
                "status": "plan",
                "version": 1,
            },
            resolve_map=self._resolve_map(),
        )
        runtime, _, _ = _runtime(hass, client, v2=False, shadow=True)

        result = await runtime.async_process(_input("Acenda a luz."))

        self.assertEqual(result.code, "success")
        self.assertEqual(len(client.resolve_calls), 7)
        self.assertEqual(
            sorted(runtime._shadow_counters), sorted(SHADOW_COUNTERS)
        )
        counters = runtime._shadow_counters
        self.assertEqual(counters["probes"], 7)
        self.assertGreaterEqual(counters["matched"], 5)
        self.assertEqual(counters["errors"], 0)

    async def test_shadow_failure_never_breaks_result(self) -> None:
        hass = _hass(_User({("light.sala", "control")}))

        class _FailingClient(_ClientV2):
            async def async_resolve(self, payload: dict[str, Any]) -> Any:
                raise ClientError("transport")

        client = _FailingClient(
            v1_response={
                "operations": [{"action": "turn_off", "targets": ["reg_light"]}],
                "status": "plan",
                "version": 1,
            }
        )
        runtime, _, _ = _runtime(hass, client, v2=False, shadow=True)

        result = await runtime.async_process(_input("Acenda a luz."))

        self.assertEqual(result.code, "success")
        self.assertGreater(runtime._shadow_counters["errors"], 0)

    async def test_shadow_disabled_by_default(self) -> None:
        hass = _hass(_User({("light.sala", "control")}))
        client = _ClientV2(
            v1_response={
                "operations": [{"action": "turn_off", "targets": ["reg_light"]}],
                "status": "plan",
                "version": 1,
            }
        )
        runtime = LocalNluRuntime(hass, client)

        await runtime.async_process(_input("Acenda a luz."))

        self.assertEqual(client.resolve_calls, [])
        self.assertTrue(all(v == 0 for v in runtime._shadow_counters.values()))


class AvailabilityTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_availability_flip_is_stale_with_zero_effects(self) -> None:
        hass = _hass(_User({("light.sala", "control")}))

        def mutate() -> None:
            hass.states._states["light.sala"].state = "unavailable"

        client = _ClientV2(
            v2_response=_v2_plan(_op("turn_off", ["reg_light"])),
            mutate=mutate,
        )
        runtime, _, _ = _runtime(hass, client, v2=True)

        result = await runtime.async_process(_input("Acenda a luz."))

        self.assertEqual(result.code, "stale")
        self.assertEqual(hass.services.calls, [])

    async def test_timeout_propagates_identically_on_both_paths(self) -> None:
        import asyncio

        class _TimeoutClient(_ClientV2):
            async def async_interpret(self, payload: dict[str, Any]) -> Any:
                raise asyncio.CancelledError()

            async def async_interpret_v2(self, payload: dict[str, Any]) -> Any:
                raise asyncio.CancelledError()

            async def async_interpret_v3(self, payload: dict[str, Any]) -> Any:
                raise asyncio.CancelledError()

        hass = _hass(_User({("light.sala", "control")}))
        for v2 in (False, True):
            runtime, _, _ = _runtime(hass, _TimeoutClient(), v2=v2)
            with self.subTest(v2=v2):
                with self.assertRaises(asyncio.CancelledError):
                    await runtime.async_process(_input("Acenda a luz."))
        self.assertEqual(hass.services.calls, [])


class OptionsConsultTests(unittest.TestCase):
    def test_options_default_off_and_consulted_per_request(self) -> None:
        options: dict[str, Any] = {}
        import asyncio

        async def run() -> tuple[LocalNluRuntime, _ClientV2, Any]:
            hass = _hass(_User({("light.sala", "control")}))
            client = _ClientV2(
                v1_response={
                    "operations": [
                        {"action": "turn_off", "targets": ["reg_light"]}
                    ],
                    "status": "plan",
                    "version": 1,
                },
                v2_response=_v2_plan(_op("turn_off", ["reg_light"])),
            )
            runtime = LocalNluRuntime(
                hass,
                client,
                lambda: bool(options.get("v2_enabled", False)),
                lambda: bool(options.get("shadow_enabled", False)),
            )
            result = await runtime.async_process(_input("Acenda a luz."))
            return runtime, client, result

        runtime, client, result = asyncio.run(run())
        self.assertEqual(result.code, "success")
        self.assertEqual(len(client.v1_calls), 1)
        self.assertEqual(client.v2_calls, [])

        options["v2_enabled"] = True
        options["shadow_enabled"] = True
        runtime, client, result = asyncio.run(run())
        _ = runtime
        self.assertEqual(result.code, "success")
        self.assertEqual(len(client.v3_calls), 1)
        self.assertEqual(client.v1_calls, [])
        self.assertGreater(len(client.resolve_calls), 0)


class ShadowPrivacyLoadTests(unittest.IsolatedAsyncioTestCase):
    def _big_hass(self, user: _User | None) -> Any:
        hass = _hass(user)
        entries = list(hass.entity_registry.entities.values())
        states = dict(hass.states._states)
        for index in range(3, 10):
            entity_id = f"light.extra{index}"
            entries.append(
                _Entry(
                    id=f"reg_extra{index}",
                    entity_id=entity_id,
                    area_id="area_sala",
                    name=f"Extra {index}",
                    original_name=f"Extra {index}",
                )
            )
            states[entity_id] = _State(
                "on", {"friendly_name": f"Extra {index}"}
            )
        hass.entity_registry.entities = {e.entity_id: e for e in entries}
        hass.states._states = states
        hass.exposed = {e.entity_id for e in entries}
        return hass

    async def test_probe_load_is_bounded(self) -> None:
        hass = self._big_hass(_User({("light.sala", "control")}))
        client = _ClientV2(
            v1_response={
                "operations": [{"action": "turn_off", "targets": ["reg_light"]}],
                "status": "plan",
                "version": 1,
            }
        )
        runtime, _, _ = _runtime(hass, client, v2=False, shadow=True)

        await runtime.async_process(_input("Acenda a luz."))

        self.assertEqual(len(client.resolve_calls), 7)
        self.assertEqual(runtime._shadow_counters["probes"], 7)

    async def test_shadow_emits_no_content(self) -> None:
        hass = _hass(_User({("light.sala", "control")}))
        client = _ClientV2(
            v1_response={
                "operations": [{"action": "turn_off", "targets": ["reg_light"]}],
                "status": "plan",
                "version": 1,
            },
            resolve_map={
                "light.sala": {
                    "outcome": "resolved",
                    "registry_id": "reg_light",
                    "evidence": "external_entity_id",
                }
            },
        )
        runtime, _, _ = _runtime(hass, client, v2=False, shadow=True)

        with self.assertLogs("custom_components.local_nlu.runtime", level="DEBUG") as logs:
            await runtime.async_process(_input("Acenda a Luz da Sala secreta."))

        combined = "\n".join(logs.output)
        for secret in (
            "reg_light",
            "light.sala",
            "Luz da sala",
            "Acenda a Luz da Sala secreta",
            "secreta",
        ):
            self.assertNotIn(secret, combined)


if __name__ == "__main__":
    unittest.main()
