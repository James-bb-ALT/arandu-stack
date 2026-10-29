"""FIXTURE_TECNICA: contextual acceptance families and HA speech boundary."""
from __future__ import annotations

import importlib
import os
import json
from pathlib import Path
import sys
from types import SimpleNamespace, ModuleType
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

try:
    import test_contextual as fixtures
    from test_contextual import Client, HttpClient, add, hass_, input_, operation, plan
except ImportError:
    from tests.mlp import test_contextual as fixtures
    from tests.mlp.test_contextual import Client, HttpClient, add, hass_, input_, operation, plan
from custom_components.local_nlu.contextual_runtime import ContextualRuntime
from custom_components.local_nlu.diagnostics import async_get_config_entry_diagnostics
from custom_components.local_nlu.contextual_catalog import build
from custom_components.local_nlu.contextual_protocol import parse
from custom_components.local_nlu.protocol import ProtocolError


class DiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_version_pairing_and_private_data_absent(self):
        version = json.loads(Path("custom_components/local_nlu/manifest.json").read_text())["version"]
        class Backend:
            async def async_diagnostics(self):
                return {"service_version": version, "protocols": [1, 2, 3, 4], "build_id": "fixture", "execution": "passive"}
        runtime = SimpleNamespace(_client=Backend(), _contextual_enabled=lambda: True, _v2_enabled=lambda: False)
        entry = SimpleNamespace(runtime_data=runtime)
        value = await async_get_config_entry_diagnostics(None, entry)
        self.assertTrue(value["compatible"])
        self.assertEqual(value["route"], "/v4/interpret")
        self.assertNotIn("options", value)
        runtime._contextual_enabled = lambda: False
        self.assertEqual((await async_get_config_entry_diagnostics(None, entry))["protocol"], 1)
        version = "0.0.0"
        self.assertEqual((await async_get_config_entry_diagnostics(None, entry))["warning"], "integration_addon_version_mismatch")

    async def test_specific_failure_speech_and_backend_failure(self):
        for reason, expected in [("unknown_area", "cômodo"), ("no_accessible_sensor", "sensor acessível"), ("sensor_unavailable", "indisponível"), ("invalid_measurement", "medição válida")]:
            runtime = ContextualRuntime(hass_(), Client({"version": 4, "status": "unavailable", "reason": reason}))
            result = await runtime.process(input_("qual a temperatura da sala"))
            self.assertEqual(result.reason, reason)
            self.assertIn(expected, result.response_text)
            self.assertFalse(result.continue_conversation)

    async def test_conversation_result_preserves_final_speech_and_id(self):
        conversation = ModuleType("homeassistant.components.conversation")
        conversation.ConversationEntity = type("Entity", (), {})
        conversation.AbstractConversationAgent = type("Agent", (), {})
        conversation.ConversationEntityFeature = SimpleNamespace(CONTROL=1)
        conversation.ConversationResult = lambda **kw: SimpleNamespace(**kw)
        intent = ModuleType("homeassistant.helpers.intent")
        class Response:
            def __init__(self, **kw):
                self.error = None
            def async_set_speech(self, speech):
                self.speech = speech
            def async_set_error(self, error, speech):
                self.error, self.speech = error, speech
        intent.IntentResponse = Response
        intent.IntentResponseType = SimpleNamespace(QUERY_ANSWER="query")
        intent.IntentResponseErrorCode = SimpleNamespace(UNKNOWN="unknown", NO_VALID_TARGETS="target", NO_INTENT_MATCH="no_match")
        modules = {"homeassistant.components.conversation": conversation, "homeassistant.helpers.intent": intent,
                   "homeassistant.config_entries": SimpleNamespace(ConfigEntry=object), "homeassistant.core": SimpleNamespace(HomeAssistant=object),
                   "homeassistant.helpers.entity_platform": SimpleNamespace(AddConfigEntryEntitiesCallback=object)}
        with patch.dict(sys.modules, modules):
            from custom_components.local_nlu import runtime as runtime_module
            module = importlib.import_module("custom_components.local_nlu.conversation")
            for code, followup, speech in [("missing_slot", True, "Em qual cômodo?"), ("unavailable", False, "O sensor está indisponível.")]:
                expected = runtime_module.RuntimeResult(code, response_text=speech, continue_conversation=followup, conversation_id="answer", reason="sensor_unavailable")
                class Runtime:
                    async def async_process(self, user_input):
                        return expected
                entity = module.LocalNluConversationEntity(SimpleNamespace(runtime_data=Runtime(), entry_id="fixture"))
                result = await entity.async_process(input_())
                self.assertEqual((result.response.speech, result.conversation_id, result.continue_conversation), (speech, "answer", followup))
                self.assertEqual(result.response.error is None, followup)


class RelationalContractTests(unittest.TestCase):
    def test_explicit_person_binding_only_enriches_authorized_device_rows(self):
        hass = hass_()
        entry = add(hass, "battery", "sensor.battery", "Carga genérica", {"device_class": "battery", "unit_of_measurement": "%"}, "70")
        entry.device_id = "mobile"
        options = {"person_device_bindings": {"telefone de Ana": ["mobile"]}}
        self.assertIn("telefone de Ana", build(hass, hass.auth.user, options).by_id["battery"]["aliases"])
        hass.exposed.remove("sensor.battery")
        self.assertNotIn("battery", build(hass, hass.auth.user, options).by_id)

    def test_set_options_contract_is_bounded_and_cannot_execute_on_clarification(self):
        command = {"intent": "fan.turn_off", "action": "turn_off", "domains": ["fan"], "mention": "ventiladores de Ana", "area": None, "device_class": None, "plural": True, "origin": False, "parameters": {}}
        response = {"version": 4, "status": "clarification", "command": command, "reason": "name_area_sets_differ", "options": [
            {"key": "name", "targets": ["a", "b"], "area": None, "evidence": ["compositional_name"]},
            {"key": "area", "targets": ["a", "b", "c"], "area": "studio", "evidence": ["qualified_area_tokens"]}]}
        self.assertEqual(len(parse(response).options), 2)
        self.assertFalse(parse(response).operations)
        response["options"][1]["targets"] *= 20
        with self.assertRaises(ProtocolError):
            parse(response)


@unittest.skipUnless(os.environ.get("ARANDU_NLU_BINARY"), "requires real Rust HTTP binary")
class ContextualHttpTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.EndToEndTests.setUpClass()
        cls.endpoint = fixtures.EndToEndTests.endpoint

    @classmethod
    def tearDownClass(cls):
        fixtures.EndToEndTests.tearDownClass()

    def room(self, count=3):
        hass = hass_()
        hass.entity_registry.entities.clear()
        hass.states._states.clear()
        hass.exposed.clear()
        hass.area_registry.areas["area_sala"].name = "Estúdio de Ana"
        for n in range(count):
            add(hass, f"fan_{n:02}", f"fan.item_{n}", f"Ventilador {n}", {"supported_features": 49}, "on")
        return hass

    async def test_area_family_controls_all_eligible_devices_and_never_locks_or_sensors(self):
        for count in (1, 3):
            for text in ("desliga o Estúdio de Ana", "desliga tudo no Estúdio de Ana", "desliga todos os aparelhos do Estúdio de Ana", "apaga o Estúdio de Ana"):
                with self.subTest(count=count, text=text):
                    hass = self.room(count)
                    add(hass, "lock", "lock.access", "Acesso", {}, "locked")
                    add(hass, "sensor", "sensor.fixture", "Medição", {}, "20")
                    result = await ContextualRuntime(hass, HttpClient(self.endpoint)).process(input_(text))
                    self.assertEqual(result.code, "success", result)
                    self.assertEqual({call["target"]["entity_id"][0] for call in hass.services.calls}, {f"fan.item_{n}" for n in range(count)})
                    self.assertFalse(result.continue_conversation)
                    self.assertIn("Pronto", result.response_text)

    async def test_large_area_refuses_entire_set_explicitly_and_exclusions_are_honored(self):
        hass = self.room(33)
        runtime = ContextualRuntime(hass, HttpClient(self.endpoint))
        result = await runtime.process(input_("desliga o Estúdio de Ana"))
        self.assertEqual(result.reason, "target_limit", result)
        self.assertIn("32", result.response_text)
        self.assertFalse(hass.services.calls)
        options = {"excluded_from_bulk_actions": [f"fan.item_{n}" for n in range(1, 33)]}
        result = await ContextualRuntime(hass, HttpClient(self.endpoint), lambda: options).process(input_("desliga tudo no Estúdio de Ana"))
        self.assertEqual(result.code, "success", result)
        self.assertEqual(len(hass.services.calls), 1)

    async def test_bulk_skips_initially_unavailable_member(self):
        hass = self.room()
        hass.states._states["fan.item_2"].state = "unavailable"
        result = await ContextualRuntime(hass, HttpClient(self.endpoint)).process(input_("desliga o Estúdio de Ana"))
        self.assertEqual(result.code, "success", result)
        self.assertEqual({call["target"]["entity_id"][0] for call in hass.services.calls}, {"fan.item_0", "fan.item_1"})

    async def test_light_category_never_expands_to_all_appliances(self):
        hass = self.room()
        add(hass, "light", "light.fixture", "Luz de bancada", {}, "on")
        result = await ContextualRuntime(hass, HttpClient(self.endpoint)).process(input_("desliga todas as luzes do Estúdio de Ana"))
        self.assertEqual(result.code, "success", result)
        self.assertEqual([call["target"]["entity_id"] for call in hass.services.calls], [["light.fixture"]])

    async def test_new_bulk_member_after_planning_invalidates_whole_plan(self):
        hass = self.room()
        class ChangingClient(HttpClient):
            async def async_interpret_v4(self, payload):
                value = await super().async_interpret_v4(payload)
                add(hass, "new", "fan.new", "Novo aparelho", {"supported_features": 49}, "on")
                return value
        result = await ContextualRuntime(hass, ChangingClient(self.endpoint)).process(input_("desliga o Estúdio de Ana"))
        self.assertEqual(result.reason, "bulk_catalog_changed", result)
        self.assertFalse(hass.services.calls)

    async def test_metric_families_prepositions_aliases_and_explicit_scope(self):
        for owner, room in [("Ana", "Estúdio de Ana"), ("João", "Laboratório de João")]:
            for metric, cls, unit, value in [("temperatura", "temperature", "°C", "25.3"), ("umidade", "humidity", "%", "61"), ("luminosidade", "illuminance", "lx", "120"), ("CO2", "carbon_dioxide", "ppm", "700")]:
                hass = self.room(0)
                hass.area_registry.areas["area_sala"].name = room
                add(hass, "reading", "sensor.reading", "Medição", {"device_class": cls, "unit_of_measurement": unit}, value)
                runtime = ContextualRuntime(hass, HttpClient(self.endpoint), lambda: {"default_area": "area_quarto"})
                for text in [*(f"qual a {metric} {prep} {room}" for prep in ("no", "na", "do", "da", "de", "em")), f"{room}, qual a {metric}?", f"quanto está a {metric} do {room}?"]:
                    with self.subTest(text=text):
                        result = await runtime.process(input_(text, conversation_id=text))
                        self.assertEqual(result.code, "query_success", result)
                        self.assertIn(value.replace(".", ","), result.response_text)
                        self.assertIn(room, result.response_text)
                        self.assertFalse(hass.services.calls)
                runtime = ContextualRuntime(hass, HttpClient(self.endpoint), lambda: {"default_area": "area_sala"})
                self.assertEqual((await runtime.process(input_(f"quanto tá de {metric} aqui"))).code, "query_success")

    async def test_measurement_sources_unavailable_invalid_and_preferred(self):
        hass = self.room(0)
        runtime = ContextualRuntime(hass, HttpClient(self.endpoint))
        result = await runtime.process(input_("qual a temperatura no Estúdio de Ana"))
        self.assertEqual(result.reason, "no_accessible_sensor", result)
        add(hass, "reading", "sensor.reading", "Termômetro", {"device_class": "temperature", "unit_of_measurement": "°C"}, "unavailable")
        runtime.catalog.invalidate()
        result = await runtime.process(input_("qual a temperatura no Estúdio de Ana"))
        self.assertEqual(result.reason, "entity_unavailable", result)
        self.assertIn("indisponível", result.response_text)
        hass.states._states["sensor.reading"].state = "not_numeric"
        runtime.catalog.invalidate()
        self.assertEqual((await runtime.process(input_("qual a temperatura no Estúdio de Ana"))).reason, "invalid_measurement")
        hass.states._states["sensor.reading"].state = "24"
        hass.states._states["sensor.reading"].attributes["unit_of_measurement"] = "W"
        runtime.catalog.invalidate()
        self.assertEqual((await runtime.process(input_("qual a temperatura no Estúdio de Ana"))).reason, "incompatible_units")
        hass.states._states["sensor.reading"].attributes["unit_of_measurement"] = "°C"
        add(hass, "second", "sensor.second", "Equipamento", {"device_class": "temperature", "unit_of_measurement": "°C"}, "42")
        runtime.catalog.invalidate()
        result = await runtime.process(input_("qual a temperatura no Estúdio de Ana"))
        self.assertEqual(result.reason, "ambiguous_source", result)
        self.assertTrue(result.continue_conversation)
        options = {"entity_preferences": {"sensor.reading": {"preferred": True}}}
        result = await ContextualRuntime(hass, HttpClient(self.endpoint), lambda: options).process(input_("qual a temperatura no Estúdio de Ana"))
        self.assertEqual(result.code, "query_success", result)
        self.assertIn("24", result.response_text)
        self.assertNotIn("42", result.response_text)
        hass.exposed.remove("sensor.reading")
        hass.exposed.remove("sensor.second")
        result = await ContextualRuntime(hass, HttpClient(self.endpoint)).process(input_("qual a temperatura no Estúdio de Ana"))
        self.assertEqual(result.reason, "no_accessible_sensor", result)
        self.assertNotIn("Equipamento", result.response_text)

    async def test_global_and_local_lists_include_only_authorized_matches(self):
        hass = self.room(0)
        add(hass, "light_a", "light.a", "Luz da bancada", {}, "off")
        add(hass, "light_b", "light.b", "Luz de leitura", {}, "on", "area_quarto")
        add(hass, "private", "light.private", "Privada", {}, "on")
        hass.exposed.remove("light.private")
        runtime = ContextualRuntime(hass, HttpClient(self.endpoint), lambda: {"default_area": "area_sala"})
        for text in ("tem alguma luz ligada em algum cômodo", "tem luz ligada em algum cômodo", "quais luzes estão acesas"):
            result = await runtime.process(input_(text, conversation_id=text))
            self.assertEqual(result.code, "query_success", result)
            self.assertIn("Luz de leitura", result.response_text)
            self.assertIn("Quarto", result.response_text)
            self.assertNotIn("Privada", result.response_text)
            self.assertNotIn("bancada", result.response_text)
        result = await runtime.process(input_("tem alguma luz ligada aqui"))
        self.assertTrue(result.response_text.startswith("Não."), result)
        hass.states._states["light.b"].state = "unavailable"
        runtime.catalog.invalidate()
        self.assertNotEqual((await runtime.process(input_("todas as luzes estão apagadas"))).code, "query_success")
        self.assertFalse(hass.services.calls)

    async def test_low_battery_threshold_is_local_configurable_and_unit_safe(self):
        hass = self.room(0)
        add(hass, "battery", "sensor.battery", "Tablet", {"device_class": "battery", "unit_of_measurement": "%"}, "25")
        options = {"low_battery_threshold": 30}
        runtime = ContextualRuntime(hass, HttpClient(self.endpoint), lambda: options)
        for text in ("tem alguma bateria acabando", "tem algum dispositivo com a bateria acabando"):
            result = await runtime.process(input_(text))
            self.assertEqual(result.code, "query_success", result)
            self.assertIn("Tablet", result.response_text)
            self.assertIn("25 %", result.response_text)
        self.assertFalse(hass.services.calls)

    def named_sets(self, equal=False):
        hass = self.room(0)
        add(hass, "a", "fan.a", "Ventilador de Ana", {"supported_features": 49}, "on")
        add(hass, "b", "fan.b", "Ventilador da mesa de Ana", {"supported_features": 49}, "on")
        if not equal:
            add(hass, "c", "fan.c", "Ventilador do teto", {"supported_features": 49}, "on")
        return hass

    async def test_set_choices_yes_no_counts_and_scope_have_no_initial_effect(self):
        for reply, ids in [("sim", ["fan.a", "fan.b", "fan.c"]), ("não", ["fan.a", "fan.b"]), ("os dois", ["fan.a", "fan.b"]), ("todos do Estúdio", ["fan.a", "fan.b", "fan.c"]), ("os três do Estúdio", ["fan.a", "fan.b", "fan.c"]), ("só os que têm Ana no nome", ["fan.a", "fan.b"])]:
            with self.subTest(reply=reply):
                hass = self.named_sets()
                runtime = ContextualRuntime(hass, HttpClient(self.endpoint))
                result = await runtime.process(input_("desliga os ventiladores de Ana"))
                self.assertEqual(result.reason, "name_area_sets_differ", result)
                self.assertTrue(result.continue_conversation)
                self.assertIn("3", result.response_text)
                self.assertFalse(hass.services.calls)
                result = await runtime.process(input_(reply))
                self.assertEqual(result.code, "success", result)
                self.assertEqual([call["target"]["entity_id"][0] for call in hass.services.calls], ids)
                count = len(hass.services.calls)
                self.assertNotEqual((await runtime.process(input_("sim"))).code, "success")
                self.assertEqual(len(hass.services.calls), count)
        hass = self.named_sets(equal=True)
        result = await ContextualRuntime(hass, HttpClient(self.endpoint)).process(input_("desliga os ventiladores de Ana"))
        self.assertEqual(result.code, "success", result)
        self.assertEqual(len(hass.services.calls), 2)

    async def test_set_pending_invalidated_by_inventory_permissions_origin_options_and_ttl(self):
        for mutation in ("inventory", "permission", "origin", "options", "ttl"):
            hass = self.named_sets()
            options = {"default_area": "area_sala"}
            runtime = ContextualRuntime(hass, HttpClient(self.endpoint), lambda: options)
            await runtime.process(input_("desliga os ventiladores de Ana"))
            if mutation == "inventory":
                add(hass, "d", "fan.d", "Ventilador extra", {"supported_features": 49}, "on")
            elif mutation == "permission":
                hass.exposed.remove("fan.c")
            elif mutation == "origin":
                options["default_area"] = "area_quarto"
            elif mutation == "options":
                options["session_ttl"] = 50
            else:
                runtime.sessions[("user", "text", "fixture")].expires = 0
            result = await runtime.process(input_("sim"))
            self.assertEqual(result.code, "stale", (mutation, result))
            self.assertIn(result.reason, ("dialogue_changed", "dialogue_expired"))
            self.assertFalse(hass.services.calls)

    async def test_source_dialogue_and_partial_command_recovery(self):
        hass = self.room(0)
        for rid, name, value in [("a", "Ambiente", "24"), ("b", "Equipamento", "45")]:
            add(hass, rid, f"sensor.{rid}", name, {"device_class": "temperature", "unit_of_measurement": "°C"}, value)
        runtime = ContextualRuntime(hass, HttpClient(self.endpoint))
        question = await runtime.process(input_("qual a temperatura no Estúdio de Ana"))
        self.assertEqual(question.reason, "ambiguous_source", question)
        self.assertIn("Qual sensor", question.response_text)
        answer = await runtime.process(input_("Ambiente"))
        self.assertEqual(answer.code, "query_success", answer)
        self.assertIn("24", answer.response_text)
        self.assertFalse(hass.services.calls)
        hass = self.named_sets(equal=True)
        runtime = ContextualRuntime(hass, HttpClient(self.endpoint))
        question = await runtime.process(input_("desliga"))
        self.assertEqual(question.reason, "missing_target", question)
        self.assertFalse(hass.services.calls)
        answer = await runtime.process(input_("o Estúdio de Ana"))
        self.assertEqual(answer.code, "success", answer)
        self.assertEqual(len(hass.services.calls), 2)

    async def test_new_independent_command_discards_set_pending(self):
        hass = self.named_sets()
        add(hass, "light", "light.fixture", "Bancada", {}, "on")
        runtime = ContextualRuntime(hass, HttpClient(self.endpoint))
        await runtime.process(input_("desliga os ventiladores de Ana"))
        result = await runtime.process(input_("apaga Bancada"))
        self.assertEqual(result.code, "success", result)
        self.assertEqual(hass.services.calls[0]["target"]["entity_id"], ["light.fixture"])
        self.assertNotEqual((await runtime.process(input_("sim"))).code, "success")
        self.assertEqual(len(hass.services.calls), 1)

    async def test_infinitive_and_polite_new_commands_replace_old_selection(self):
        for command in ("desligar Bancada", "pode desligar Bancada", "por favor desligar Bancada", "aciona Bancada"):
            hass = self.named_sets()
            add(hass, "light", "light.fixture", "Bancada", {}, "on")
            runtime = ContextualRuntime(hass, HttpClient(self.endpoint))
            await runtime.process(input_("desliga os ventiladores de Ana"))
            result = await runtime.process(input_(command))
            self.assertEqual(result.code, "success", (command, result))
            self.assertEqual([call["target"]["entity_id"] for call in hass.services.calls], [["light.fixture"]])
            self.assertNotEqual((await runtime.process(input_("sim"))).code, "success")
            self.assertEqual(len(hass.services.calls), 1)

    async def test_verb_free_query_discards_old_selection_and_acesa_synonym(self):
        hass = self.named_sets()
        add(hass, "light", "light.fixture", "Bancada", {}, "on")
        runtime = ContextualRuntime(hass, HttpClient(self.endpoint))
        await runtime.process(input_("desliga os ventiladores de Ana"))
        result = await runtime.process(input_("todos os ventiladores estão desligados"))
        self.assertEqual(result.code, "query_success", result)
        self.assertFalse(hass.services.calls)
        self.assertNotEqual((await runtime.process(input_("sim"))).code, "success")
        for text in ("tem alguma luz acesa", "tem luz acesa"):
            result = await runtime.process(input_(text))
            self.assertEqual(result.code, "query_success", result)
            self.assertIn("Bancada", result.response_text)
        self.assertFalse(hass.services.calls)

    async def test_aggregate_floor_questions_keep_real_membership_scope(self):
        hass = self.room(1)
        add(hass, "light", "light.fixture", "Bancada", {}, "on")
        add(hass, "other", "light.other", "Outro cômodo", {}, "on", "area_quarto")
        hass.area_registry.areas["area_sala"].floor_id = "floor_1"
        floors = SimpleNamespace(async_get=lambda _: SimpleNamespace(floors={"floor_1": SimpleNamespace(name="Área social", aliases=set())}))
        with patch.dict(sys.modules, {"homeassistant.helpers.floor_registry": floors}):
            runtime = ContextualRuntime(hass, HttpClient(self.endpoint))
            for text in ("quais luzes estão ligadas na área social", "tem alguma coisa ligada na área social"):
                result = await runtime.process(input_(text))
                self.assertEqual(result.code, "query_success", result)
                self.assertIn("Bancada", result.response_text)
                self.assertNotIn("Outro cômodo", result.response_text)
            self.assertFalse(hass.services.calls)
    async def test_switch_plural_qualified_sets_and_same_named_room_device(self):
        for category in ("interruptores", "tomadas"):
            hass = self.room(0)
            singular = "Interruptor" if category == "interruptores" else "Tomada"
            for rid, name in [("a", f"{singular} de Ana"), ("b", f"{singular} da mesa de Ana"), ("c", f"{singular} do teto")]:
                add(hass, rid, f"switch.{rid}", name, {"device_class": "outlet"} if category == "tomadas" else {}, "on")
            runtime = ContextualRuntime(hass, HttpClient(self.endpoint))
            question = await runtime.process(input_(f"desliga todos os {category} de Ana"))
            self.assertEqual(question.reason, "name_area_sets_differ", question)
            self.assertFalse(hass.services.calls)
            self.assertEqual((await runtime.process(input_("não"))).code, "success")
            self.assertEqual([call["target"]["entity_id"] for call in hass.services.calls], [["switch.a"], ["switch.b"]])
        for command in ("apaga o Estúdio de Ana", "desliga tudo no Estúdio de Ana"):
            hass = self.room(1)
            add(hass, "light", "light.fixture", "Bancada", {}, "on")
            add(hass, "robot", "vacuum.robot", "Estúdio de Ana", {"supported_features": 8}, "cleaning")
            hass.services.available.add(("vacuum", "stop"))
            result = await ContextualRuntime(hass, HttpClient(self.endpoint)).process(input_(command))
            self.assertEqual(result.code, "success", result)
            self.assertEqual({call["target"]["entity_id"][0] for call in hass.services.calls}, {"fan.item_0", "light.fixture"})

    async def test_preferred_source_never_selects_between_homonymous_devices(self):
        for metric in ("bateria", "localização"):
            hass = self.room(0)
            devices = {did: SimpleNamespace(name="Telefone de Ana", name_by_user=None, area_id=None) for did in ("d1", "d2")}
            hass.device_registry.async_get = devices.get
            for rid, value in [("a", "30"), ("b", "60")]:
                entry = add(hass, rid, f"sensor.{rid}", f"Carga {rid}", {"device_class": "battery", "unit_of_measurement": "%"}, value, None)
                entry.device_id = "d1" if rid == "a" else "d2"
            options = {"entity_preferences": {"sensor.a": {"preferred": True}}}
            question = "qual a bateria do telefone de Ana" if metric == "bateria" else "onde está o telefone de Ana"
            result = await ContextualRuntime(hass, HttpClient(self.endpoint), lambda: options).process(input_(question))
            self.assertEqual(result.reason, "ambiguous_source", result)
            self.assertIn("Qual aparelho", result.response_text)
            self.assertTrue(result.continue_conversation)
            self.assertFalse(hass.services.calls)

    async def test_clarification_revalidates_names_and_sets_after_inference_or_cached_snapshot(self):
        for sets in (False, True):
            for cached in (False, True):
                hass = self.named_sets() if sets else self.room(0)
                if not sets:
                    for rid in ("a", "b"):
                        add(hass, rid, f"sensor.{rid}", f"Fonte privada {rid}", {"device_class": "temperature", "unit_of_measurement": "°C"}, "24")
                class RevokingClient(HttpClient):
                    async def async_interpret_v4(self, payload):
                        value = await super().async_interpret_v4(payload)
                        if not cached:
                            hass.exposed.clear()
                        return value
                runtime = ContextualRuntime(hass, RevokingClient(self.endpoint))
                if cached:
                    runtime.catalog.get("user", hass.auth.user, {})
                    hass.exposed.clear()
                result = await runtime.process(input_("desliga os ventiladores de Ana" if sets else "qual a temperatura no Estúdio de Ana"))
                self.assertEqual(result.reason, "clarification_catalog_changed", result)
                self.assertFalse(result.continue_conversation)
                self.assertNotIn("privada", result.response_text)
                self.assertNotIn("Ana", result.response_text)
                self.assertFalse(hass.services.calls)

    async def test_personal_alias_identity_is_not_replaced_by_preferred_or_available_other_device(self):
        for mode in ("normal", "preferred_other", "unavailable_identified"):
            hass = self.room(0)
            devices = {"d1": SimpleNamespace(name="Telefone pessoal", name_by_user=None, area_id=None), "d2": SimpleNamespace(name="Telefone de Ana", name_by_user=None, area_id=None)}
            hass.device_registry.async_get = devices.get
            for rid, value in [("a", "30"), ("b", "60")]:
                entry = add(hass, rid, f"sensor.{rid}", "Carga", {"device_class": "battery", "unit_of_measurement": "%"}, value, None)
                entry.device_id = "d1" if rid == "a" else "d2"
            options = {"entity_preferences": {"sensor.a": {"aliases": ["telefone de Ana"]}, "sensor.b": {"preferred": mode == "preferred_other"}}}
            if mode == "unavailable_identified":
                hass.states._states["sensor.a"].state = "unavailable"
            result = await ContextualRuntime(hass, HttpClient(self.endpoint), lambda: options).process(input_("qual a bateria do telefone de Ana"))
            if mode == "unavailable_identified":
                # Existing preflight contract reports stale/entity_unavailable.
                self.assertEqual((result.code, result.reason), ("stale", "entity_unavailable"), result)
                self.assertIn("indisponível", result.response_text)
            else:
                self.assertEqual(result.code, "query_success", result)
                self.assertIn("30 %", result.response_text)
            self.assertNotIn("60 %", result.response_text)
            self.assertFalse(hass.services.calls)

    async def test_existing_humidity_global_and_outlet_families_keep_operational_behavior(self):
        hass = self.room(0)
        add(hass, "humidity", "sensor.humidity", "Ambiente", {"device_class": "humidity", "unit_of_measurement": "%"}, "61")
        result = await ContextualRuntime(hass, HttpClient(self.endpoint)).process(input_("me fala quanto tá de umidade no Estúdio de Ana"))
        self.assertEqual(result.code, "query_success", result)
        self.assertIn("61", result.response_text)
        for text in ("desliga tudo", "apaga a casa", "desliga a casa toda", "pode desligar tudo"):
            hass = self.room(2)
            options = {"excluded_from_bulk_actions": ["fan.item_1"]}
            result = await ContextualRuntime(hass, HttpClient(self.endpoint), lambda: options).process(input_(text))
            self.assertEqual(result.code, "success", result)
            self.assertEqual([call["target"]["entity_id"] for call in hass.services.calls], [["fan.item_0"]])
        for text in ("liga a tomada", "desliga a tomada"):
            hass = self.room(0)
            add(hass, "outlet", "switch.outlet", "Tomada", {"device_class": "outlet"}, "off")
            result = await ContextualRuntime(hass, HttpClient(self.endpoint)).process(input_(text))
            self.assertEqual(result.code, "success", result)
            self.assertEqual(hass.services.calls[0]["target"]["entity_id"], ["switch.outlet"])

    async def test_invalid_set_choice_and_cross_session_never_execute(self):
        hass = self.named_sets()
        runtime = ContextualRuntime(hass, HttpClient(self.endpoint))
        await runtime.process(input_("desliga os ventiladores de Ana"))
        for answer in ("todos da cozinha", "só os que têm Bruna no nome", "talvez"):
            result = await runtime.process(input_(answer))
            self.assertTrue(result.continue_conversation, result)
            self.assertFalse(hass.services.calls)
        result = await runtime.process(input_("sim", conversation_id="other"))
        self.assertNotEqual(result.code, "success", result)
        self.assertFalse(hass.services.calls)
        result = await runtime.process(input_("sim", satellite_id="different"))
        self.assertNotEqual(result.code, "success", result)
        self.assertFalse(hass.services.calls)

    async def test_named_device_short_answer_and_permission_change_during_source_dialogue(self):
        hass = self.named_sets()
        runtime = ContextualRuntime(hass, HttpClient(self.endpoint))
        question = await runtime.process(input_("desliga o ventilador"))
        self.assertTrue(question.continue_conversation, question)
        answer = await runtime.process(input_("da mesa"))
        self.assertEqual(answer.code, "success", answer)
        self.assertEqual(hass.services.calls[0]["target"]["entity_id"], ["fan.b"])
        hass = self.room(0)
        add(hass, "a", "sensor.a", "Ambiente", {"device_class": "temperature", "unit_of_measurement": "°C"}, "24")
        add(hass, "b", "sensor.b", "Equipamento", {"device_class": "temperature", "unit_of_measurement": "°C"}, "45")
        runtime = ContextualRuntime(hass, HttpClient(self.endpoint))
        await runtime.process(input_("qual a temperatura no Estúdio de Ana"))
        hass.exposed.remove("sensor.b")
        result = await runtime.process(input_("Ambiente"))
        self.assertEqual(result.reason, "dialogue_changed", result)
        self.assertFalse(hass.services.calls)

    def mobile(self, name="Telefone de Ana", provider=True):
        hass = self.room(0)
        phone = SimpleNamespace(name=name, name_by_user=None, area_id="area_quarto")
        hass.device_registry.async_get = lambda did: phone if did == "mobile" else None
        battery = add(hass, "battery", "sensor.opaque", "Carga genérica", {"device_class": "battery", "unit_of_measurement": "%"}, "73", None)
        battery.device_id = "mobile"
        if provider:
            location = add(hass, "location", "sensor.dynamic", "Área Bluetooth", {"device_class": "bermuda__custom_device_class", "area_id": "area_sala", "last_seen": datetime.now(timezone.utc).isoformat()}, "Estúdio de Ana", None)
            location.device_id, location.platform, location.original_name = "mobile", "bermuda", "Area"
        return hass

    async def test_personal_battery_uses_device_relationship_not_sensor_name(self):
        for name, request in [("Telefone de Ana", "qual a bateria do celular de Ana"), ("Tablet de João", "quanto de bateria está o tablet de João")]:
            hass = self.mobile(name)
            result = await ContextualRuntime(hass, HttpClient(self.endpoint)).process(input_(request))
            self.assertEqual(result.code, "query_success", result)
            self.assertIn(name, result.response_text)
            self.assertIn("73 %", result.response_text)
            self.assertFalse(hass.services.calls)
            hass.states._states["sensor.opaque"].state = "unavailable"
            result = await ContextualRuntime(hass, HttpClient(self.endpoint)).process(input_(request))
            self.assertIn("indisponível", result.response_text)
            hass.exposed.remove("sensor.opaque")
            result = await ContextualRuntime(hass, HttpClient(self.endpoint)).process(input_(request))
            self.assertEqual(result.reason, "no_accessible_sensor", result)

    async def test_dynamic_location_never_uses_registered_area_and_requires_observation(self):
        hass = self.mobile()
        runtime = ContextualRuntime(hass, HttpClient(self.endpoint))
        result = await runtime.process(input_("onde está o telefone de Ana"))
        self.assertEqual(result.code, "query_success", result)
        self.assertIn("Estúdio de Ana", result.response_text)
        self.assertIn("observado há", result.response_text)
        self.assertNotIn("Quarto", result.response_text)
        state = hass.states._states["sensor.dynamic"]
        state.attributes["area_id"], state.state = "area_quarto", "Quarto"
        result = await runtime.process(input_("em qual cômodo está o celular de Ana"))
        self.assertEqual(result.code, "query_success", result)
        self.assertIn("Quarto", result.response_text)
        state.attributes["last_seen"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        self.assertEqual((await runtime.process(input_("onde está o telefone de Ana"))).reason, "location_stale")
        state.attributes.pop("last_seen")
        state.last_changed = datetime.now(timezone.utc)
        self.assertEqual((await runtime.process(input_("onde está o telefone de Ana"))).reason, "location_stale")
        state.attributes["last_seen"] = datetime.now(timezone.utc).isoformat()
        state.attributes["area_id"] = "not_registered"
        self.assertEqual((await runtime.process(input_("onde está o telefone de Ana"))).reason, "location_unknown_area")
        self.assertFalse(hass.services.calls)

    async def test_missing_location_provider_unknown_device_and_hidden_provider(self):
        hass = self.mobile(provider=False)
        result = await ContextualRuntime(hass, HttpClient(self.endpoint)).process(input_("onde está o telefone de Ana"))
        self.assertEqual(result.reason, "no_location_provider", result)
        self.assertNotIn("Quarto", result.response_text)
        result = await ContextualRuntime(hass, HttpClient(self.endpoint)).process(input_("onde está o telefone de Pessoa inexistente"))
        self.assertEqual(result.reason, "device_not_identified", result)
        hass = self.mobile()
        hass.exposed.remove("sensor.dynamic")
        result = await ContextualRuntime(hass, HttpClient(self.endpoint)).process(input_("onde está o telefone de Ana"))
        self.assertEqual(result.reason, "no_location_provider", result)
        self.assertNotIn("Bluetooth", result.response_text)

    async def test_explicit_cross_device_binding_and_timestamp_entity(self):
        hass = self.mobile(provider=False)
        add(hass, "location", "sensor.beacon", "Localização sem nome pessoal", {}, "Quarto", None).device_id = "ble_device"
        add(hass, "observed", "sensor.seen", "Observação", {"device_class": "timestamp"}, datetime.now(timezone.utc).isoformat(), None)
        options = {"person_device_bindings": {"meu celular": ["mobile"]}, "device_sensor_bindings": {"mobile": {"location": "sensor.beacon", "observed_at_entity": "sensor.seen", "max_age_seconds": 60}}}
        runtime = ContextualRuntime(hass, HttpClient(self.endpoint), lambda: options)
        result = await runtime.process(input_("onde está meu celular"))
        self.assertEqual(result.code, "query_success", result)
        self.assertIn("Quarto", result.response_text)
        hass.exposed.remove("sensor.seen")
        runtime.catalog.invalidate()
        result = await runtime.process(input_("onde está meu celular"))
        self.assertEqual(result.reason, "location_stale", result)
        self.assertFalse(hass.services.calls)

    async def test_homonymous_personal_devices_ask_and_source_choice_is_revalidated(self):
        hass = self.mobile("Telefone pessoal", provider=False)
        other = SimpleNamespace(name="Telefone profissional", name_by_user=None, area_id=None)
        first = hass.device_registry.async_get("mobile")
        hass.device_registry.async_get = lambda did: first if did == "mobile" else other if did == "work_mobile" else None
        add(hass, "work", "sensor.work", "Carga", {"device_class": "battery", "unit_of_measurement": "%"}, "45", None).device_id = "work_mobile"
        options = {"person_device_bindings": {"telefone de Ana": ["mobile", "work_mobile"]}}
        runtime = ContextualRuntime(hass, HttpClient(self.endpoint), lambda: options)
        question = await runtime.process(input_("qual a bateria do telefone de Ana"))
        self.assertEqual(question.reason, "ambiguous_source", question)
        self.assertIn("Telefone profissional", question.response_text)
        result = await runtime.process(input_("telefone profissional"))
        self.assertEqual(result.code, "query_success", result)
        self.assertIn("45 %", result.response_text)
        self.assertNotIn("73", result.response_text)

    async def test_location_sources_ambiguous_and_observation_invalid_or_future(self):
        hass = self.mobile()
        extra = add(hass, "extra", "sensor.other_location", "Outra área", {"device_class": "bermuda__custom_device_class", "area_id": "area_quarto", "last_seen": datetime.now(timezone.utc).isoformat()}, "Quarto", None)
        extra.device_id, extra.platform, extra.original_name = "mobile", "bermuda", "Area"
        result = await ContextualRuntime(hass, HttpClient(self.endpoint)).process(input_("onde está o telefone de Ana"))
        self.assertEqual(result.reason, "ambiguous_source", result)
        self.assertTrue(result.continue_conversation)
        hass.exposed.remove("sensor.other_location")
        for value in ("not_a_timestamp", datetime.now().isoformat(), (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()):
            hass.states._states["sensor.dynamic"].attributes["last_seen"] = value
            result = await ContextualRuntime(hass, HttpClient(self.endpoint)).process(input_("onde está o telefone de Ana"))
            self.assertEqual(result.reason, "location_stale", result)
        self.assertFalse(hass.services.calls)

    async def test_battery_freshness_policy_and_invalid_percent_never_invent_reading(self):
        hass = self.mobile(provider=False)
        options = {"entity_preferences": {"sensor.opaque": {"max_age_seconds": 60, "observed_at_attribute": "last_seen"}}}
        runtime = ContextualRuntime(hass, HttpClient(self.endpoint), lambda: options)
        result = await runtime.process(input_("qual a bateria do telefone de Ana"))
        self.assertEqual(result.reason, "measurement_stale", result)
        state = hass.states._states["sensor.opaque"]
        state.attributes["last_seen"] = datetime.now(timezone.utc).isoformat()
        result = await runtime.process(input_("qual a bateria do telefone de Ana"))
        self.assertEqual(result.code, "query_success", result)
        state.state = "120"
        result = await runtime.process(input_("qual a bateria do telefone de Ana"))
        self.assertEqual(result.reason, "invalid_measurement", result)
        self.assertFalse(hass.services.calls)

    async def test_area_alias_collisions_climate_measured_fallback_and_setpoint_separation(self):
        hass = self.room(0)
        hass.area_registry.areas["area_sala"].aliases.add("Ambiente")
        hass.area_registry.areas["area_quarto"].aliases.add("Ambiente")
        result = await ContextualRuntime(hass, HttpClient(self.endpoint)).process(input_("qual a temperatura no Ambiente"))
        self.assertEqual(result.reason, "ambiguous_area", result)
        self.assertTrue(result.continue_conversation)
        add(hass, "climate", "climate.fixture", "Climatização", {"current_temperature": 26, "temperature": 19}, "cool")
        result = await ContextualRuntime(hass, HttpClient(self.endpoint)).process(input_("qual a temperatura no Estúdio de Ana"))
        self.assertEqual(result.code, "query_success", result)
        self.assertIn("26 graus", result.response_text)
        self.assertNotIn("19", result.response_text)
        hass.states._states["climate.fixture"].attributes.pop("current_temperature")
        result = await ContextualRuntime(hass, HttpClient(self.endpoint)).process(input_("qual a temperatura no Estúdio de Ana"))
        self.assertEqual(result.reason, "no_accessible_sensor", result)
        self.assertFalse(hass.services.calls)

    async def test_excluded_group_and_critical_metadata_never_enter_collective_set(self):
        hass = self.room()
        add(hass, "group", "group.critical", "Infraestrutura", {"entity_id": ["fan.item_1"]}, "on")
        options = {"excluded_from_bulk_actions": ["group.critical"], "entity_preferences": {"fan.item_2": {"critical": True}}}
        result = await ContextualRuntime(hass, HttpClient(self.endpoint), lambda: options).process(input_("desliga o Estúdio de Ana"))
        self.assertEqual(result.code, "success", result)
        self.assertEqual([call["target"]["entity_id"] for call in hass.services.calls], [["fan.item_0"]])

    async def test_none_aggregate_and_explicit_measurement_semantics_are_truthful(self):
        hass = self.room(0)
        add(hass, "light", "light.fixture", "Bancada", {}, "off")
        runtime = ContextualRuntime(hass, HttpClient(self.endpoint))
        result = await runtime.process(input_("nenhuma das luzes está ligada"))
        self.assertEqual(result.response_text, "Sim, nenhuma.", result)
        hass.states._states["light.fixture"].state = "on"
        result = await runtime.process(input_("nenhuma das luzes está ligada"))
        self.assertIn("Bancada", result.response_text)
        self.assertTrue(result.response_text.startswith("Não."), result)
        add(hass, "temperature", "sensor.fixture", "Temperatura do chip", {"device_class": "temperature", "unit_of_measurement": "°C"}, "45")
        options = {"entity_preferences": {"sensor.fixture": {"measurement_kind": "equipment"}}}
        result = await ContextualRuntime(hass, HttpClient(self.endpoint), lambda: options).process(input_("qual a temperatura no Estúdio de Ana"))
        self.assertEqual(result.reason, "no_accessible_sensor", result)
        self.assertFalse(hass.services.calls)
        hass = self.room()
        class RevokingClient(HttpClient):
            async def async_interpret_v4(self, payload):
                value = await super().async_interpret_v4(payload)
                hass.exposed.remove("fan.item_2")
                return value
        result = await ContextualRuntime(hass, RevokingClient(self.endpoint)).process(input_("desliga tudo no Estúdio de Ana"))
        self.assertEqual(result.code, "denied", result)
        self.assertFalse(hass.services.calls)
