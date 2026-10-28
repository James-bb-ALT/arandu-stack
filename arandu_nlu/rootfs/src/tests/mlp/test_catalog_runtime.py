"""FIXTURE_TECNICA catalog, authorization, and execution tests."""

from __future__ import annotations

import sys
import types
import unittest
from dataclasses import dataclass
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

    modules = {
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
    sys.modules.update(modules)


_install_home_assistant_fakes()

from custom_components.local_nlu.catalog import build_catalog  # noqa: E402
from custom_components.local_nlu.runtime import LocalNluRuntime  # noqa: E402


@dataclass
class _Entry:
    id: str
    entity_id: str
    area_id: str | None
    aliases: list[str]
    name: str | None
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
            "area_quarto": SimpleNamespace(
                name="Quarto", aliases={"Dormitório"}
            ),
            "area_sala": SimpleNamespace(
                name="Sala", aliases={"Sala de estar"}
            ),
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
            ("switch", "turn_off"),
            ("switch", "turn_on"),
        }
        self.calls: list[dict[str, Any]] = []
        self.after_call = after_call

    def has_service(self, domain: str, service: str) -> bool:
        return (domain, service) in self.available

    async def async_call(
        self,
        domain: str,
        service: str,
        data: dict[str, Any],
        *,
        blocking: bool,
        context: Any,
        target: dict[str, Any],
    ) -> None:
        self.calls.append(
            {
                "blocking": blocking,
                "context": context,
                "data": data,
                "domain": domain,
                "service": service,
                "target": target,
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
    def __init__(
        self,
        allowed: set[tuple[str, str]],
        *,
        admin: bool = False,
    ) -> None:
        self.is_active = True
        self.is_admin = admin
        self.permissions = _Permissions(allowed)


class _Auth:
    def __init__(self, user: _User | None) -> None:
        self.user = user

    async def async_get_user(self, _: str) -> _User | None:
        return self.user


class _Client:
    def __init__(
        self,
        response: dict[str, Any],
        mutate: Any = None,
    ) -> None:
        self.response = response
        self.mutate = mutate
        self.payloads: list[dict[str, Any]] = []

    async def async_interpret(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.payloads.append(payload)
        if self.mutate is not None:
            self.mutate()
        return self.response


def _entry(
    registry_id: str,
    entity_id: str,
    area_id: str,
    name: str,
) -> _Entry:
    return _Entry(
        id=registry_id,
        entity_id=entity_id,
        area_id=area_id,
        aliases=[],
        name=name,
        original_name=name,
    )


def _hass(user: _User | None = None) -> Any:
    entries = [
        _entry("reg_fan", "fan.ventilador", "area_quarto", "Ventilador"),
        _entry("reg_light", "light.sala", "area_sala", "Luz da sala"),
        _entry(
            "reg_sensor",
            "sensor.temperatura",
            "area_sala",
            "Temperatura da sala",
        ),
        _entry(
            "reg_switch",
            "switch.cafeteira",
            "area_sala",
            "Cafeteira",
        ),
    ]
    states = {
        "fan.ventilador": _State(
            "on",
            {"friendly_name": "Ventilador", "supported_features": 49},
        ),
        "light.sala": _State("on", {"friendly_name": "Luz da sala"}),
        "sensor.temperatura": _State(
            "23.5",
            {
                "friendly_name": "Temperatura da sala",
                "unit_of_measurement": "°C",
            },
        ),
        "switch.cafeteira": _State(
            "off", {"friendly_name": "Cafeteira"}
        ),
    }
    hass = SimpleNamespace(
        area_registry=_Areas(),
        auth=_Auth(user),
        device_registry=_Devices(),
        entity_registry=_Registry(entries),
        exposed={entry.entity_id for entry in entries},
        services=_Services(),
        states=_States(states),
    )
    return hass


def _input() -> Any:
    context = SimpleNamespace(user_id="FIXTURE_TECNICA_USER")
    return SimpleNamespace(
        context=context,
        text="FIXTURE_TECNICA utterance",
    )


class CatalogTests(unittest.TestCase):
    def test_catalog_is_sorted_bounded_and_capability_specific(self) -> None:
        snapshot = build_catalog(_hass())

        ids = [
            row["registry_id"] for row in snapshot.payload["entities"]
        ]
        self.assertEqual(ids, sorted(ids))
        fan = next(
            row
            for row in snapshot.payload["entities"]
            if row["registry_id"] == "reg_fan"
        )
        sensor = next(
            row
            for row in snapshot.payload["entities"]
            if row["registry_id"] == "reg_sensor"
        )
        self.assertIn("set_fan_percentage", fan["actions"])
        self.assertEqual(sensor["actions"], ["get_state"])

    def test_unexposed_unavailable_and_disabled_entities_are_absent(self) -> None:
        hass = _hass()
        hass.exposed.remove("switch.cafeteira")
        hass.states._states["sensor.temperatura"].state = "unavailable"
        hass.entity_registry.entities["light.sala"].disabled_by = "user"

        snapshot = build_catalog(hass)

        self.assertEqual(
            [row["registry_id"] for row in snapshot.payload["entities"]],
            ["reg_fan"],
        )

    def test_speed_only_fan_does_not_advertise_turn_actions(self) -> None:
        hass = _hass()
        hass.states._states["fan.ventilador"].attributes[
            "supported_features"
        ] = 1

        snapshot = build_catalog(hass)
        fan = next(
            row
            for row in snapshot.payload["entities"]
            if row["registry_id"] == "reg_fan"
        )

        self.assertEqual(
            fan["actions"],
            ["get_state", "set_fan_percentage"],
        )


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_preflights_complete_chain_then_executes_in_order(self) -> None:
        allowed = {
            ("light.sala", "control"),
            ("switch.cafeteira", "control"),
        }
        hass = _hass(_User(allowed))
        client = _Client(
            {
                "operations": [
                    {"action": "turn_off", "targets": ["reg_light"]},
                    {"action": "turn_on", "targets": ["reg_switch"]},
                ],
                "status": "plan",
                "version": 1,
            }
        )
        runtime = LocalNluRuntime(hass, client)
        user_input = _input()

        result = await runtime.async_process(user_input)

        self.assertEqual(result.code, "success")
        self.assertEqual(result.operation_count, 2)
        self.assertEqual(
            [
                (call["domain"], call["service"])
                for call in hass.services.calls
            ],
            [("light", "turn_off"), ("switch", "turn_on")],
        )
        self.assertTrue(
            all(
                call["context"] is user_input.context
                for call in hass.services.calls
            )
        )

    async def test_denied_second_operation_blocks_entire_plan(self) -> None:
        hass = _hass(_User({("light.sala", "control")}))
        client = _Client(
            {
                "operations": [
                    {"action": "turn_off", "targets": ["reg_light"]},
                    {"action": "turn_on", "targets": ["reg_switch"]},
                ],
                "status": "plan",
                "version": 1,
            }
        )

        result = await LocalNluRuntime(hass, client).async_process(_input())

        self.assertEqual(result.code, "denied")
        self.assertEqual(hass.services.calls, [])

    async def test_fan_percentage_uses_closed_service_and_value(self) -> None:
        hass = _hass(_User({("fan.ventilador", "control")}))
        client = _Client(
            {
                "operations": [
                    {
                        "action": "set_fan_percentage",
                        "percentage": 45,
                        "targets": ["reg_fan"],
                    }
                ],
                "status": "plan",
                "version": 1,
            }
        )

        result = await LocalNluRuntime(hass, client).async_process(_input())

        self.assertEqual(result.code, "success")
        self.assertEqual(
            hass.services.calls[0]["data"], {"percentage": 45}
        )
        self.assertEqual(hass.services.calls[0]["service"], "set_percentage")

    async def test_speed_only_fan_blocks_entire_turn_chain(self) -> None:
        hass = _hass(
            _User(
                {
                    ("fan.ventilador", "control"),
                    ("light.sala", "control"),
                }
            )
        )
        hass.states._states["fan.ventilador"].attributes[
            "supported_features"
        ] = 1
        client = _Client(
            {
                "operations": [
                    {"action": "turn_off", "targets": ["reg_light"]},
                    {"action": "turn_off", "targets": ["reg_fan"]},
                ],
                "status": "plan",
                "version": 1,
            }
        )

        result = await LocalNluRuntime(hass, client).async_process(_input())

        self.assertEqual(result.code, "stale")
        self.assertEqual(hass.services.calls, [])

    async def test_sensor_query_preserves_unit_without_service_call(self) -> None:
        hass = _hass(_User({("sensor.temperatura", "read")}))
        client = _Client(
            {
                "operations": [
                    {"action": "get_state", "targets": ["reg_sensor"]}
                ],
                "status": "plan",
                "version": 1,
            }
        )

        result = await LocalNluRuntime(hass, client).async_process(_input())

        self.assertEqual(result.code, "query_success")
        self.assertEqual(result.states[0].state, "23.5")
        self.assertEqual(result.states[0].unit, "°C")
        self.assertEqual(hass.services.calls, [])

    async def test_query_label_uses_bounded_catalog_name(self) -> None:
        hass = _hass(_User({("sensor.temperatura", "read")}))
        hass.states._states["sensor.temperatura"].attributes[
            "friendly_name"
        ] = "x" * 1_000_000
        client = _Client(
            {
                "operations": [
                    {"action": "get_state", "targets": ["reg_sensor"]}
                ],
                "status": "plan",
                "version": 1,
            }
        )

        result = await LocalNluRuntime(hass, client).async_process(_input())

        self.assertEqual(result.code, "query_success")
        self.assertEqual(result.states[0].label, "Temperatura da sala")

    async def test_each_domain_batch_rechecks_exposure_and_permission(self) -> None:
        allowed = {
            ("fan.ventilador", "control"),
            ("light.sala", "control"),
        }
        hass = _hass(_User(allowed))
        hass.services.after_call = lambda: hass.exposed.remove("light.sala")
        client = _Client(
            {
                "operations": [
                    {
                        "action": "turn_off",
                        "targets": ["reg_fan", "reg_light"],
                    }
                ],
                "status": "plan",
                "version": 1,
            }
        )

        result = await LocalNluRuntime(hass, client).async_process(_input())

        self.assertEqual(result.code, "stale")
        self.assertEqual(
            [
                (call["domain"], call["service"])
                for call in hass.services.calls
            ],
            [("fan", "turn_off")],
        )

    async def test_catalog_change_after_interpretation_blocks_execution(self) -> None:
        hass = _hass(_User({("light.sala", "control")}))

        def mutate() -> None:
            hass.states._states["light.sala"].attributes[
                "friendly_name"
            ] = "Nome alterado"

        client = _Client(
            {
                "operations": [
                    {"action": "turn_off", "targets": ["reg_light"]}
                ],
                "status": "plan",
                "version": 1,
            },
            mutate=mutate,
        )

        result = await LocalNluRuntime(hass, client).async_process(_input())

        self.assertEqual(result.code, "stale")
        self.assertEqual(hass.services.calls, [])

    async def test_addon_cannot_inject_target_outside_sent_catalog(self) -> None:
        hass = _hass(_User({("light.sala", "control")}))
        hidden = _entry(
            "reg_hidden", "light.hidden", "area_sala", "Hidden"
        )
        hass.entity_registry.entities[hidden.entity_id] = hidden
        hass.states._states[hidden.entity_id] = _State(
            "off", {"friendly_name": "Hidden"}
        )
        client = _Client(
            {
                "operations": [
                    {"action": "turn_on", "targets": ["reg_hidden"]}
                ],
                "status": "plan",
                "version": 1,
            }
        )

        result = await LocalNluRuntime(hass, client).async_process(_input())

        self.assertEqual(result.code, "stale")
        self.assertEqual(hass.services.calls, [])


if __name__ == "__main__":
    unittest.main()
