"""FIXTURE_TECNICA entity-resolution snapshot builder tests."""

from __future__ import annotations

import hashlib
import json
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
            "homeassistant.components": components,
            "homeassistant.components.homeassistant": homeassistant_component,
            "homeassistant.components.homeassistant.exposed_entities": exposed,
        }
    )


_install_home_assistant_fakes()

from custom_components.local_nlu.catalog import (  # noqa: E402
    CatalogError,
    build_er_snapshot,
)


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
            "area_sala": SimpleNamespace(name="Sala", aliases={"Estar"}),
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
    def __init__(self) -> None:
        self.available = {
            ("fan", "set_percentage"),
            ("fan", "turn_off"),
            ("fan", "turn_on"),
            ("light", "turn_off"),
            ("light", "turn_on"),
            ("switch", "turn_off"),
            ("switch", "turn_on"),
        }

    def has_service(self, domain: str, service: str) -> bool:
        return (domain, service) in self.available


def _hass() -> Any:
    entries = [
        _Entry(
            id="reg_lamp",
            entity_id="light.abajur",
            area_id="area_sala",
            aliases=["Abajur da sala", "Luminaria extra"],
            name="Nome de registro",
            original_name="Nome original",
        ),
        _Entry(
            id="reg_fan",
            entity_id="fan.ventilador",
            area_id="area_quarto",
            name="Ventilador",
        ),
    ]
    states = {
        "light.abajur": _State(
            "on", {"friendly_name": "Abajur", "supported_features": 0}
        ),
        "fan.ventilador": _State(
            "on",
            {"friendly_name": "Ventilador", "supported_features": 49},
        ),
    }
    hass = SimpleNamespace(
        area_registry=_Areas(),
        device_registry=_Devices(),
        entity_registry=_Registry(entries),
        exposed={entry.entity_id for entry in entries},
        services=_Services(),
        states=_States(states),
    )
    return hass


def _recompute_generation(payload: dict[str, Any]) -> str:
    descriptors = {k: v for k, v in payload.items() if k != "generation"}
    canonical = json.dumps(
        descriptors, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class ErSnapshotTests(unittest.TestCase):
    def test_display_aliases_and_generation_shape(self) -> None:
        snapshot = build_er_snapshot(_hass())
        payload = snapshot.payload

        self.assertEqual(payload["catalog_id"], "ha-entity-resolution-v1")
        self.assertEqual(payload["generation"], _recompute_generation(payload))
        self.assertEqual(len(payload["generation"]), 64)

        lamp = next(
            row for row in payload["entities"] if row["registry_id"] == "reg_lamp"
        )
        self.assertEqual(lamp["display_name"], "Abajur")
        self.assertEqual(lamp["aliases"], ["Abajur da sala", "Luminaria extra"])
        self.assertEqual(lamp["entity_id"], "light.abajur")
        self.assertEqual(lamp["area_id"], "area_sala")
        fan = next(
            row for row in payload["entities"] if row["registry_id"] == "reg_fan"
        )
        self.assertIn("set_fan_percentage", fan["capabilities"])
        self.assertEqual(
            [row["registry_id"] for row in payload["entities"]],
            ["reg_fan", "reg_lamp"],
        )

    def test_display_fallback_order_is_normative(self) -> None:
        hass = _hass()
        state = hass.states._states["light.abajur"]
        state.attributes.pop("friendly_name")

        snapshot = build_er_snapshot(hass)
        lamp = next(
            row
            for row in snapshot.payload["entities"]
            if row["registry_id"] == "reg_lamp"
        )
        self.assertEqual(lamp["display_name"], "Nome de registro")

        entry = hass.entity_registry.entities["light.abajur"]
        entry.name = None
        entry.original_name_unprefixed = "Sem prefixo"
        snapshot = build_er_snapshot(hass)
        lamp = next(
            row
            for row in snapshot.payload["entities"]
            if row["registry_id"] == "reg_lamp"
        )
        self.assertEqual(lamp["display_name"], "Sem prefixo")

    def test_displayless_unavailable_unexposed_disabled_unregistered_absent(
        self,
    ) -> None:
        hass = _hass()
        hass.exposed.remove("fan.ventilador")
        hass.states._states["light.abajur"].state = "unavailable"
        hass.states._states["ghost"] = _State("on", {"friendly_name": "Ghost"})

        snapshot = build_er_snapshot(hass)
        self.assertEqual(snapshot.payload["entities"], [])

        hass = _hass()
        hass.entity_registry.entities["light.abajur"].disabled_by = "user"
        state = hass.states._states["fan.ventilador"]
        state.attributes.pop("friendly_name")
        hass.entity_registry.entities["fan.ventilador"].name = None

        snapshot = build_er_snapshot(hass)
        self.assertEqual(snapshot.payload["entities"], [])

    def test_generation_flips_on_descriptors_not_on_values(self) -> None:
        hass = _hass()
        before = build_er_snapshot(hass).payload["generation"]

        hass.states._states["fan.ventilador"].state = "off"
        hass.states._states["fan.ventilador"].attributes["extra"] = "x"
        self.assertEqual(
            build_er_snapshot(hass).payload["generation"], before
        )

        hass.states._states["fan.ventilador"].attributes["friendly_name"] = (
            "Ventuinha"
        )
        renamed = build_er_snapshot(hass).payload["generation"]
        self.assertNotEqual(renamed, before)

        hass = _hass()
        hass.entity_registry.entities["fan.ventilador"].area_id = "area_sala"
        rebound = build_er_snapshot(hass).payload["generation"]
        self.assertNotEqual(rebound, before)

    def test_build_is_reproducible(self) -> None:
        hass = _hass()
        first = json.dumps(
            build_er_snapshot(hass).payload, sort_keys=True, ensure_ascii=False
        )
        second = json.dumps(
            build_er_snapshot(hass).payload, sort_keys=True, ensure_ascii=False
        )
        self.assertEqual(first, second)

    def test_oversized_catalog_fails_closed(self) -> None:
        hass = _hass()
        entries = [
            _Entry(id=f"reg_{i}", entity_id=f"light.e{i}", name=f"Luz {i}")
            for i in range(1_025)
        ]
        hass.entity_registry = _Registry(entries)
        hass.states = _States(
            {e.entity_id: _State("on", {"friendly_name": f"Luz {i}"}) for i, e in enumerate(entries)}
        )
        hass.exposed = {e.entity_id for e in entries}

        with self.assertRaises(CatalogError):
            build_er_snapshot(hass)


if __name__ == "__main__":
    unittest.main()
