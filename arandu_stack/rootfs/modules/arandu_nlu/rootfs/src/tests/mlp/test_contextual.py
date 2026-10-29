"""FIXTURE_TECNICA. HA executor tests plus real Rust HTTP conformance when built."""
from __future__ import annotations

import asyncio
import copy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.request

try:
    from test_catalog_runtime import _hass, _entry, _State, _User
except ImportError:
    from tests.mlp.test_catalog_runtime import _hass, _entry, _State, _User
from custom_components.local_nlu.contextual_catalog import build, CatalogCache
from custom_components.local_nlu.contextual_runtime import ContextualRuntime
from custom_components.local_nlu.contextual_protocol import parse
from custom_components.local_nlu.protocol import ProtocolError
from custom_components.local_nlu.capabilities import ADAPTERS, parameters, CapabilityError
from custom_components.local_nlu.queries import render, QueryError


def operation(action="turn_on", targets=None, params=None, intent=None, depends=None):
    return {"action": action, "intent": intent or "fixture." + action, "targets": targets if targets is not None else ["reg_light"], "parameters": params or {}, "evidence": ["friendly_name"], "depends_on": depends or []}


def plan(*operations):
    return {"version": 4, "status": "plan", "operations": list(operations)}


class Client:
    def __init__(self, response):
        self.response = response
        self.catalogs = []
        self.requests = []
        self.after = None

    async def async_catalog_v4(self, payload):
        self.catalogs.append(payload)
        return {"version": 4, "status": "catalog_ready", "generation": payload["generation"]}

    async def async_interpret_v4(self, payload):
        self.requests.append(payload)
        if self.after:
            self.after()
        return copy.deepcopy(self.response)


def input_(text="Liga a luz", conversation_id="fixture", user_id="user", satellite_id=None):
    return SimpleNamespace(text=text, context=SimpleNamespace(user_id=user_id), conversation_id=conversation_id, device_id=None, satellite_id=satellite_id, language="pt-BR")


def hass_():
    hass = _hass(_User(set(), admin=True))
    hass.states._states["light.sala"].attributes.update(supported_color_modes=["brightness", "rgb", "color_temp"], brightness=128, min_color_temp_kelvin=2000, max_color_temp_kelvin=6500, color_temp_kelvin=4000)
    hass.states._states["fan.ventilador"].attributes["percentage"] = 50
    hass.states._states["sensor.temperatura"].attributes["device_class"] = "temperature"
    hass.config = SimpleNamespace(time_zone="America/Bahia", units=SimpleNamespace(temperature_unit="°C"))
    return hass


def add(hass, registry_id, entity_id, name, attributes, state="off", area="area_sala"):
    entry = _entry(registry_id, entity_id, area, name)
    hass.entity_registry.entities[entity_id] = entry
    hass.states._states[entity_id] = _State(state, {"friendly_name": name, **attributes})
    hass.exposed.add(entity_id)
    return entry


class ProtocolTests(unittest.TestCase):
    def test_power_inventory_does_not_count_motion_or_an_open_cover(self):
        values = [{"domain": domain, "value": value} for domain, value in [("binary_sensor", "on"), ("cover", "open"), ("climate", "heat_cool"), ("media_player", "idle")]]
        self.assertEqual(render(values, {"state_filter": "on", "aggregate": "count"}, {}), "Encontrei 2.")

    def test_qualitative_extremes_use_device_limits_and_minimum_speed(self):
        fan = _State("on", {"percentage_step": 25})
        self.assertEqual(parameters("percentage", ADAPTERS[("fan", "percentage")], fan, {"value": "minimum"}, {}), {"percentage": 25})
        climate = _State("cool", {"min_temp": 16, "max_temp": 30, "target_temp_step": .5})
        self.assertEqual(parameters("temperature", ADAPTERS[("climate", "temperature")], climate, {"value": "maximum"}, {}), {"temperature": 30})
    def test_untrusted_operations_and_parameters_rejected(self):
        for bad in [plan(operation("shell")), plan(operation(params={"service": "delete"})), plan(operation(params={"value": float("nan")})), plan(operation(targets=["reg_light", "reg_light"])), plan(operation(depends=[0])), plan(operation(targets=[]))]:
            with self.subTest(bad=bad), self.assertRaises(ProtocolError):
                parse(bad)

    def test_relative_parameters_read_live_value_and_clamp(self):
        state = _State("on", {"percentage": 95})
        self.assertEqual(parameters("percentage", ADAPTERS[("fan", "percentage")], state, {"relative": 1}, {}), {"percentage": 100})
        state.attributes["percentage"] = 40
        self.assertEqual(parameters("percentage", ADAPTERS[("fan", "percentage")], state, {"relative": -1}, {"percentage": 5}), {"percentage": 35})
        state.attributes.pop("percentage")
        with self.assertRaises(CapabilityError):
            parameters("percentage", ADAPTERS[("fan", "percentage")], state, {"relative": 1}, {})

    def test_energy_sum_requires_non_overlapping_explicit_sources(self):
        values = [{"entity_id": "sensor.a", "device_id": "a", "value": 10, "unit": "W"}, {"entity_id": "sensor.b", "device_id": "b", "value": 20, "unit": "W"}]
        with self.assertRaises(QueryError):
            render(values, {"metric": "power", "aggregate": "sum"}, {})
        self.assertIn("30", render(values, {"metric": "power", "aggregate": "sum"}, {"energy_sources": ["sensor.a", "sensor.b"]}))
        values[1]["unit"] = "kW"
        with self.assertRaises(QueryError):
            render(values, {"metric": "power", "aggregate": "sum"}, {"energy_sources": ["sensor.a", "sensor.b"]})


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_replaced_binding_policy_during_inference_is_revoked(self):
        hass = hass_()
        add(hass, "binding", "script.fixture", "Backend", {})
        hass.services.available.add(("script", "turn_on"))
        options = {"intent_bindings": {"camera.view": {"entity_id": "script.fixture", "sensitive": False}}}
        client = Client(plan(operation("camera_view", [], intent="camera.view")))
        def revoke():
            nonlocal options
            options = {}
        client.after = revoke
        result = await ContextualRuntime(hass, client, lambda: options).process(input_())
        self.assertEqual(result.code, "stale", result)
        self.assertEqual(result.reason, "options_changed")
        self.assertFalse(hass.services.calls)

    async def test_list_response_requires_requested_entities_and_explicit_empty_list(self):
        for domain, action, service, field in [("calendar", "calendar", "get_events", "events"), ("todo", "todo_list", "get_items", "items"), ("weather", "forecast", "get_forecasts", "forecast")]:
            hass = hass_()
            entity_id = domain + ".fixture"
            add(hass, "target", entity_id, "Lista", {"supported_features": 1})
            hass.services.available.add((domain, service))
            response = {}
            async def call(*args, **kwargs):
                return response
            hass.services.async_call = call
            runtime = ContextualRuntime(hass, Client(plan(operation(action, ["target"]))))
            for malformed in [{}, {entity_id: {}}, {entity_id: {field: []}, domain + ".private": {field: []}}, {entity_id: {field: [None]}}]:
                response = malformed
                result = await runtime.process(input_())
                self.assertNotEqual(result.code, "query_success", (action, malformed, result))
            if action != "forecast":
                response = {entity_id: {field: []}}
                self.assertEqual((await runtime.process(input_())).code, "query_success")

    async def test_failed_service_acknowledgement_does_not_repeat_attempt(self):
        hass = hass_()
        original = hass.services.async_call
        async def uncertain(*args, **kwargs):
            await original(*args, **kwargs)
            raise TimeoutError
        hass.services.async_call = uncertain
        runtime = ContextualRuntime(hass, Client(plan(operation())))
        first = await runtime.process(input_())
        self.assertEqual(first.code, "execution_failed", first)
        self.assertEqual(first.operation_count, 0)
        self.assertEqual((await runtime.process(input_())).code, "cancelled")
        self.assertEqual(len(hass.services.calls), 1)

    async def test_relative_multi_target_rechecks_each_value_after_prior_call(self):
        hass = hass_()
        add(hass, "reg_fan2", "fan.second", "Segundo ventilador", {"supported_features": 49, "percentage": 50})
        hass.services.after_call = lambda: hass.states._states["fan.second"].attributes.update(percentage=90)
        runtime = ContextualRuntime(hass, Client(plan(operation("percentage", ["reg_fan", "reg_fan2"], {"relative": 1}))))
        result = await runtime.process(input_())
        self.assertEqual(result.code, "partial_failure", result)
        self.assertEqual(result.reason, "parameters_changed")
        self.assertEqual(len(hass.services.calls), 1)

    async def test_empty_registered_area_is_visible_as_cleaning_destination(self):
        hass = hass_()
        hass.area_registry.areas["empty_room"] = SimpleNamespace(name="Área vazia", aliases=set())
        self.assertIn("empty_room", {area["area_id"] for area in build(hass, hass.auth.user, {}).payload["areas"]})

    async def test_origin_changes_during_inference_abstain(self):
        hass = hass_()
        options = {"default_area": "area_sala"}
        client = Client(plan(operation()))
        client.after = lambda: options.update(default_area="area_quarto")
        result = await ContextualRuntime(hass, client, lambda: options).process(input_())
        self.assertEqual(result.code, "stale", result)
        self.assertFalse(hass.services.calls)
    async def test_person_room_requires_linked_tracker_and_its_permission(self):
        hass = hass_()
        add(hass, "person", "person.bruna", "Bruna", {}, "home", None)
        add(hass, "indoor", "sensor.bruna_room", "Localização interna", {}, "area_quarto", None)
        client = Client(plan(operation("query", ["person"], {"metric": "location", "area": "area_sala"})))
        options = {}
        runtime = ContextualRuntime(hass, client, lambda: options)
        self.assertEqual((await runtime.process(input_())).code, "unavailable")
        options["entity_preferences"] = {"person.bruna": {"indoor_tracker": "sensor.bruna_room"}}
        result = await runtime.process(input_())
        self.assertEqual(result.code, "query_success", result)
        self.assertTrue(result.response_text.startswith("Não."))
        self.assertIn("quarto", result.response_text.lower())
        hass.exposed.remove("sensor.bruna_room")
        self.assertEqual((await runtime.process(input_())).code, "denied")
        self.assertFalse(hass.services.calls)
    async def test_configured_binding_preserves_slots_and_refuses_unmapped_values(self):
        hass = hass_()
        add(hass, "script", "script.alarm", "Alarme de relógio", {})
        hass.services.available.add(("script", "turn_on"))
        op = operation("binding", [], {"value": {"time": "sete e meia"}})
        op["intent"] = "alarm.set"
        options = {"intent_bindings": {"alarm.set": {"entity_id": "script.alarm", "sensitive": False}}}
        runtime = ContextualRuntime(hass, Client(plan(op)), lambda: options)
        result = await runtime.process(input_())
        self.assertEqual(result.code, "unavailable")
        self.assertFalse(hass.services.calls)
        options["intent_bindings"]["alarm.set"]["variables"] = {"time": "horario"}
        result = await runtime.process(input_())
        self.assertEqual(result.code, "success", result)
        self.assertEqual(hass.services.calls[-1]["data"], {"variables": {"horario": "sete e meia"}})

    async def test_light_query_and_relative_execute_real_fields(self):
        hass = hass_()
        client = Client(plan(operation("brightness", params={"relative": -1})))
        runtime = ContextualRuntime(hass, client)
        result = await runtime.process(input_())
        self.assertEqual(result.code, "success", result)
        self.assertEqual(hass.services.calls[0]["data"], {"brightness_pct": 40})
        self.assertEqual(len(client.catalogs), 1)
        client.response = plan(operation("query", ["reg_sensor"], {"metric": "temperature"}))
        result = await runtime.process(input_("Qual a temperatura?"))
        self.assertEqual(result.code, "query_success", result)
        self.assertIn("23,5 graus", result.response_text)
        self.assertEqual(len(hass.services.calls), 1)
        self.assertEqual(len(client.catalogs), 1)

    async def test_exposure_permission_and_capability_revalidated_after_nlu(self):
        for mutation, code in [(lambda h: h.exposed.clear(), "denied"), (lambda h: h.states._states["light.sala"].attributes.update(supported_color_modes=["onoff"]), "stale"), (lambda h: setattr(h.auth.user, "is_active", False), "denied")]:
            hass = hass_()
            client = Client(plan(operation("brightness", params={"value": 50})))
            client.after = lambda: mutation(hass)
            result = await ContextualRuntime(hass, client).process(input_())
            self.assertEqual(result.code, code, result)
            self.assertEqual(hass.services.calls, [])

    async def test_complete_preflight_and_partial_failure(self):
        hass = hass_()
        client = Client(plan(operation(), operation("percentage", ["reg_fan"], {"value": 200}, depends=[0])))
        result = await ContextualRuntime(hass, client).process(input_())
        self.assertEqual(result.code, "unavailable")
        self.assertEqual(hass.services.calls, [])
        client.response = plan(operation(), operation("percentage", ["reg_fan"], {"value": 30}, depends=[0]))
        hass.services.after_call = lambda: hass.exposed.remove("fan.ventilador")
        result = await ContextualRuntime(hass, client).process(input_())
        self.assertEqual(result.code, "partial_failure", result)
        self.assertEqual(result.operation_count, 1)
        self.assertEqual(len(hass.services.calls), 1)

    async def test_confirmation_is_bound_to_user_origin_and_ttl(self):
        hass = hass_()
        add(hass, "reg_lock", "lock.front", "Porta", {})
        hass.services.available.add(("lock", "unlock"))
        client = Client(plan(operation("unlock", ["reg_lock"])))
        runtime = ContextualRuntime(hass, client, lambda: {"sensitive_entities": ["lock.front"]})
        result = await runtime.process(input_("Destranca a porta", satellite_id="sat.one"))
        self.assertEqual(result.code, "confirmation_required", result)
        self.assertTrue(result.continue_conversation)
        self.assertFalse(hass.services.calls)
        result = await runtime.process(input_("Sim", user_id="other", satellite_id="sat.one"))
        self.assertEqual(result.code, "stale")
        result = await runtime.process(input_("Sim", satellite_id="sat.two"))
        self.assertEqual(result.code, "stale")
        result = await runtime.process(input_("Sim", satellite_id="sat.one", conversation_id="other_conversation"))
        self.assertEqual(result.code, "stale")
        result = await runtime.process(input_("Sim", satellite_id="sat.one"))
        self.assertEqual(result.code, "success", result)
        self.assertEqual(len(hass.services.calls), 1)
        await runtime.process(input_("Destranca a porta", satellite_id="sat.one"))
        runtime.sessions[("user", "sat.one", "fixture")].expires = time.monotonic() - 1
        result = await runtime.process(input_("Sim", satellite_id="sat.one"))
        self.assertEqual(result.code, "stale")
        self.assertEqual(len(hass.services.calls), 1)

    async def test_sensitive_policy_authentication_and_retry(self):
        hass = hass_()
        add(hass, "reg_lock", "lock.front", "Porta", {"code_format": "number"})
        hass.services.available.add(("lock", "unlock"))
        client = Client(plan(operation("unlock", ["reg_lock"])))
        result = await ContextualRuntime(hass, client, lambda: {"sensitive_entities": ["lock.front"]}).process(input_())
        self.assertEqual(result.code, "denied")
        client.response = plan(operation())
        runtime = ContextualRuntime(hass, client)
        self.assertEqual((await runtime.process(input_())).code, "success")
        self.assertEqual((await runtime.process(input_())).code, "cancelled")
        self.assertEqual(len(hass.services.calls), 1)

    async def test_ambient_climate_never_reads_setpoint(self):
        hass = hass_()
        add(hass, "reg_climate", "climate.room", "Ar", {"supported_features": 385, "temperature": 18, "current_temperature": 27, "min_temp": 16, "max_temp": 30})
        client = Client(plan(operation("query", ["reg_climate"], {"metric": "temperature"})))
        result = await ContextualRuntime(hass, client).process(input_("Qual a temperatura?"))
        self.assertIn("27 graus", result.response_text)
        self.assertNotIn("18", result.response_text)

    async def test_snapshot_determinism_hidden_entities_and_session_limit(self):
        hass = hass_()
        a = build(hass, hass.auth.user, {})
        hass.entity_registry.entities = dict(reversed(list(hass.entity_registry.entities.items())))
        b = build(hass, hass.auth.user, {})
        self.assertEqual(a.payload, b.payload)
        hass.entity_registry.entities["light.sala"].hidden_by = "user"
        self.assertNotIn("reg_light", build(hass, hass.auth.user, {}).by_id)
        client = Client({"version": 4, "status": "no_match"})
        runtime = ContextualRuntime(hass, client)
        for i in range(140):
            await runtime.process(input_(conversation_id=str(i)))
        self.assertEqual(len(runtime.sessions), 128)

    async def test_preference_changes_invalidate_warm_catalog(self):
        hass = hass_()
        cache = CatalogCache(hass)
        initial = cache.get("user", hass.auth.user, {})
        updated = cache.get("user", hass.auth.user, {"entity_preferences": {"light.sala": {"preferred": True}}})
        self.assertNotEqual(initial.payload["generation"], updated.payload["generation"])
        self.assertTrue(updated.by_id["reg_light"]["preferred"])

    async def test_transfer_refuses_same_player(self):
        hass = hass_()
        entry = add(hass, "music", "media_player.music", "Som", {"supported_features": 512})
        entry.platform = "music_assistant"
        hass.services.available.add(("music_assistant", "transfer_queue"))
        result = await ContextualRuntime(hass, Client(plan(operation("transfer", ["music"], {"secondary_targets": ["music"]})))).process(input_())
        self.assertEqual(result.code, "unavailable")
        self.assertFalse(hass.services.calls)

    async def test_partial_failure_inside_one_multi_target_operation(self):
        hass = hass_()
        add(hass, "reg_light2", "light.second", "Luz dois", {"supported_color_modes": ["brightness"]})
        hass.services.after_call = lambda: hass.exposed.remove("light.second")
        runtime = ContextualRuntime(hass, Client(plan(operation(targets=["reg_light", "reg_light2"], params={"scope": "bulk_area", "area": "area_sala"}))))
        result = await runtime.process(input_())
        self.assertEqual(result.code, "partial_failure", result)
        self.assertEqual(result.operation_count, 0)
        self.assertEqual(len(hass.services.calls), 1)
        await runtime.process(input_())
        self.assertEqual(len(hass.services.calls), 1)


class HttpClient:
    def __init__(self, endpoint):
        self.endpoint = endpoint

    async def send(self, path, payload):
        def request():
            req = urllib.request.Request(self.endpoint + path, json.dumps(payload, ensure_ascii=False).encode(), {"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=3) as response:
                return json.load(response)
        return await asyncio.to_thread(request)

    async def async_catalog_v4(self, payload):
        return await self.send("/v4/catalog", payload)

    async def async_interpret_v4(self, payload):
        return await self.send("/v4/interpret", payload)


@unittest.skipUnless(os.environ.get("ARANDU_NLU_BINARY"), "Set ARANDU_NLU_BINARY to run the real Rust/HA executor path")
class EndToEndTests(unittest.IsolatedAsyncioTestCase):
    async def test_steering_explicit_climate_area_negation_and_recent_relative_target(self):
        hass = hass_()
        attrs = {"supported_features": 385, "temperature": 22, "current_temperature": 26, "min_temp": 16, "max_temp": 30}
        add(hass, "ar_sala", "climate.sala", "Ar-condicionado", attrs)
        add(hass, "ar_quarto", "climate.quarto", "Ar-condicionado", attrs, area="area_quarto")
        hass.services.available.update({("climate", "turn_on"), ("climate", "turn_off"), ("climate", "set_temperature")})
        runtime = ContextualRuntime(hass, HttpClient(self.endpoint), lambda: {"default_area": "area_sala"})
        result = await runtime.process(input_("Desliga o ar do quarto."))
        self.assertEqual(result.code, "success", result)
        self.assertEqual(hass.services.calls[-1]["target"], {"entity_id": ["climate.quarto"]})
        count = len(hass.services.calls)
        self.assertEqual((await runtime.process(input_("Não desliga o ventilador."))).code, "cancelled")
        self.assertEqual(len(hass.services.calls), count)
        result = await runtime.process(input_("Coloca o ar em 23."))
        self.assertEqual(result.code, "success", result)
        self.assertEqual(hass.services.calls[-1]["data"], {"temperature": 23})
        hass.states._states["climate.sala"].attributes["temperature"] = 23
        result = await runtime.process(input_("Aumenta dois graus."))
        self.assertEqual(result.code, "success", result)
        self.assertEqual(hass.services.calls[-1]["data"], {"temperature": 25})

    async def test_steering_all_lights_global_local_and_unknown(self):
        hass = hass_()
        hass.states._states["light.sala"].state = "off"
        add(hass, "light2", "light.quarto", "Luz do quarto", {}, "on", "area_quarto")
        runtime = ContextualRuntime(hass, HttpClient(self.endpoint), lambda: {"default_area": "area_sala"})
        for text, expected in [("Todas as luzes estão desligadas?", "Não, nem todos."), ("Todas as luzes daqui estão desligadas?", "Sim, todos.")]:
            result = await runtime.process(input_(text, conversation_id=text))
            self.assertEqual(result.code, "query_success", result)
            self.assertEqual(result.response_text, expected, result)
        hass.states._states["light.quarto"].state = "unavailable"
        runtime.catalog.invalidate(None)
        result = await runtime.process(input_("Todas as luzes estão desligadas?", conversation_id="unknown"))
        self.assertNotEqual(result.code, "query_success", result)
        self.assertFalse(hass.services.calls)

    async def test_global_aggregates_and_explicit_query_area_override_origin(self):
        hass = hass_()
        hass.states._states["sensor.temperatura"].state = "20"
        hass.states._states["light.sala"].state = "off"
        add(hass, "hot", "sensor.quarto", "Temperatura do quarto", {"device_class": "temperature", "unit_of_measurement": "°C"}, "32", "area_quarto")
        add(hass, "light2", "light.quarto", "Luz do quarto", {}, "on", "area_quarto")
        runtime = ContextualRuntime(hass, HttpClient(self.endpoint), lambda: {"default_area": "area_sala"})
        cases = [("Qual cômodo está mais quente?", "32"), ("Tem alguma luz ligada no quarto?", "Sim."), ("Quantas luzes estão ligadas no quarto?", "Encontrei 1."), ("Tem alguma luz ligada aqui?", "Não.")]
        for text, expected in cases:
            result = await runtime.process(input_(text, conversation_id=text))
            self.assertEqual(result.code, "query_success", result)
            self.assertIn(expected, result.response_text, result)
        self.assertFalse(hass.services.calls)

    async def test_unavailable_exposed_fan_prevents_false_all_off_answer(self):
        hass = hass_()
        hass.states._states["fan.ventilador"].state = "off"
        add(hass, "reg_fan2", "fan.second", "Ventilador", {"supported_features": 49}, state="unavailable")
        snapshot = build(hass, hass.auth.user, {})
        self.assertEqual(snapshot.by_id["reg_fan2"]["actions"], ["query"])
        result = await ContextualRuntime(hass, HttpClient(self.endpoint)).process(input_("todos os ventiladores estão desligados"))
        self.assertEqual(result.code, "stale", result)
        self.assertFalse(hass.services.calls)

    async def test_floor_registry_scopes_multiple_rooms_and_rechecks_membership(self):
        hass = hass_()
        hass.area_registry.areas["area_sala"].floor_id = "floor_1"
        hass.area_registry.areas["area_quarto"].floor_id = "floor_1"
        add(hass, "reg_fan2", "fan.second", "Ventilador", {"supported_features": 49}, area="area_sala")
        floors = SimpleNamespace(async_get=lambda _: SimpleNamespace(floors={"floor_1": SimpleNamespace(name="Andar superior", aliases=set())}))
        with patch.dict(sys.modules, {"homeassistant.helpers.floor_registry": floors}):
            runtime = ContextualRuntime(hass, HttpClient(self.endpoint))
            result = await runtime.process(input_("liga os ventiladores do andar superior"))
            self.assertEqual(result.code, "success", result)
            self.assertEqual(len(hass.services.calls), 2)
            hass.services.calls.clear()
            hass.services.after_call = lambda: setattr(hass.area_registry.areas["area_sala"], "floor_id", "floor_2")
            result = await runtime.process(input_("desliga os ventiladores do andar superior", conversation_id="floor_change"))
            self.assertEqual(result.code, "partial_failure", result)
            self.assertEqual(len(hass.services.calls), 1)

    @classmethod
    def setUpClass(cls):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        cls.endpoint = f"http://127.0.0.1:{port}"
        cls.server = subprocess.Popen([os.environ["ARANDU_NLU_BINARY"], "serve", "--listen", f"127.0.0.1:{port}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                with urllib.request.urlopen(cls.endpoint + "/health", timeout=.1):
                    return
            except OSError:
                time.sleep(.02)
        raise RuntimeError("Rust HTTP server did not start")

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate()
        cls.server.wait(timeout=5)

    async def test_user_examples_through_rust_http_and_authorized_executor(self):
        hass = hass_()
        add(hass, "reg_ar", "climate.room", "Ar-condicionado", {"supported_features": 385, "temperature": 22, "current_temperature": 26, "min_temp": 16, "max_temp": 30, "target_temp_step": .5})
        hass.services.available.update({("climate", "turn_on"), ("climate", "turn_off"), ("climate", "set_temperature")})
        runtime = ContextualRuntime(hass, HttpClient(self.endpoint), lambda: {"default_area": "area_sala"})
        for text in ["Liga o ar", "Coloca o ar em vinte e três graus", "Deixa a luz mais fraca", "Coloca a iluminação azul", "Liga o ar e apaga a luz"]:
            result = await runtime.process(input_(text, conversation_id=text))
            self.assertEqual(result.code, "success", f"{text}: {result}")
        result = await runtime.process(input_("Qual a temperatura aqui?", conversation_id="query"))
        self.assertEqual(result.code, "query_success", result)
        self.assertIn("23,5", result.response_text)
        previous = len(hass.services.calls)
        result = await runtime.process(input_("Não liga o ar", conversation_id="negative"))
        self.assertEqual(result.code, "cancelled", result)
        self.assertEqual(len(hass.services.calls), previous)

    async def test_ambiguity_followup_preserves_parameters(self):
        hass = hass_()
        add(hass, "reg_fan2", "fan.second", "Ventilador", {"supported_features": 49, "percentage": 20}, area="area_sala")
        runtime = ContextualRuntime(hass, HttpClient(self.endpoint))
        result = await runtime.process(input_("Coloca o ventilador em 50 por cento"))
        self.assertEqual(result.code, "missing_slot", result)
        self.assertTrue(result.continue_conversation)
        result = await runtime.process(input_("O do quarto"))
        self.assertEqual(result.code, "success", result)
        self.assertEqual(hass.services.calls[-1]["data"], {"percentage": 50})

    async def test_calendar_missing_duration_then_real_service_fields(self):
        hass = hass_()
        add(hass, "calendar", "calendar.family", "Agenda", {"supported_features": 1})
        hass.services.available.add(("calendar", "create_event"))
        runtime = ContextualRuntime(hass, HttpClient(self.endpoint))
        result = await runtime.process(input_("coloca reunião na agenda amanhã às 14 horas"))
        self.assertEqual(result.code, "missing_slot", result)
        self.assertFalse(hass.services.calls)
        result = await runtime.process(input_("trinta minutos"))
        self.assertEqual(result.code, "success", result)
        data = hass.services.calls[-1]["data"]
        self.assertEqual(data["summary"], "reunião")
        self.assertIn("T14:00:00", data["start_date_time"])
        self.assertIn("T14:30:00", data["end_date_time"])

    async def test_music_query_is_preserved_after_clarification(self):
        hass = hass_()
        entry = add(hass, "music", "media_player.music", "Som", {"supported_features": 512})
        entry.platform, entry.config_entry_id = "music_assistant", "ma"
        hass.services.available.update({("music_assistant", "search"), ("music_assistant", "play_media")})
        original = hass.services.async_call
        async def call(domain, service, data, **kwargs):
            kwargs.pop("return_response", None)
            kwargs.setdefault("target", {})
            await original(domain, service, data, **kwargs)
            if service == "search":
                return {"tracks": [{"name": "Garota de Ipanema", "uri": "spotify://track/fixture"}]}
        hass.services.async_call = call
        runtime = ContextualRuntime(hass, HttpClient(self.endpoint))
        result = await runtime.process(input_("toca no Spotify"))
        self.assertEqual(result.code, "missing_slot", result)
        result = await runtime.process(input_("Garota de Ipanema"))
        self.assertEqual(result.code, "success", result)
        self.assertEqual(hass.services.calls[-1]["data"]["media_id"], "spotify://track/fixture")
