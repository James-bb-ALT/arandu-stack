"""FIXTURE_TECNICA: spatial acceptance matrix, real Rust HTTP and simulated HA."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_contextual as fixtures
from test_contextual import Client, HttpClient, add, hass_, input_, operation, plan
from test_catalog_runtime import _User
from custom_components.local_nlu.contextual_catalog import origin_context
from custom_components.local_nlu.contextual_runtime import ContextualRuntime


def house():
    hass = hass_()
    hass.entity_registry.entities.clear()
    hass.states._states.clear()
    hass.exposed.clear()
    hass.area_registry.areas["area_cozinha"] = SimpleNamespace(name="Cozinha", aliases=set())
    for area in ("sala", "cozinha", "quarto"):
        for n in range(2):
            add(hass, f"light_{area}_{n}", f"light.{area}_{n}", f"Luminária {area} {n}", {}, area="area_" + area)
        add(hass, f"fan_{area}", f"fan.{area}", f"Ventilador {area}", {"supported_features": 49}, area="area_" + area)
        add(hass, f"sat_{area}", f"assist_satellite.{area}", f"Satélite {area}", {}, area="area_" + area)
    return hass


def called(hass):
    return {call["target"]["entity_id"][0] for call in hass.services.calls}


class ProvenanceTests(unittest.TestCase):
    def test_failed_target_label_preserves_unicode_and_excludes_other_clauses(self):
        from custom_components.local_nlu.contextual_errors import requested_target
        command = {"mention": "luz", "area": "modulo inexistente"}
        for label in ("luz do módulo inexistente", "luz do mo\u0301dulo inexistente", "luz no Módulo Inexistente"):
            original = f"liga a {label} e desliga a bancada"
            self.assertEqual(requested_target(command, original), label)
        self.assertEqual(requested_target({"mention": "a luminaria inexistente"}, "Liga a Luminária Inexistente!"), "Luminária Inexistente")
        self.assertIsNone(requested_target({"mention": None}, "liga"))
        self.assertIsNone(requested_target({"mention": "a "}, "liga"))

    def test_wake_word_cleanup_is_exact_bounded_and_preserves_input(self):
        from custom_components.local_nlu.voice_input import without_wake_word
        original = input_("Alexa, luz", satellite_id="assist_satellite.sala")
        cleaned = without_wake_word(original, {"wake_words": ["Alexa"]}, True)
        self.assertEqual((original.text, cleaned.text), ("Alexa, luz", "luz"))
        self.assertIs(cleaned.context, original.context)
        for text in ("Alexandra luz", "outra luz", "Alexa", "Alexa Alexa luz"):
            request = input_(text)
            result = without_wake_word(request, {"wake_words": ["Alexa"]}, True)
            self.assertEqual(result.text, "Alexa luz" if text == "Alexa Alexa luz" else text)
        self.assertIs(without_wake_word(original, {"wake_words": ["Alexa"]}, False), original)
        for invalid in ("Alexa", [""], ["Alexa,"], ["Alexa"] * 9):
            with self.assertRaises(ValueError):
                without_wake_word(original, {"wake_words": invalid}, True)

    def test_registered_provenance_and_default_are_distinct(self):
        hass = house()
        request = input_(satellite_id="assist_satellite.sala")
        self.assertEqual(origin_context(hass, request, {}), ("area_sala", "satellite"))
        request.satellite_id = "assist_satellite.missing"
        self.assertEqual(origin_context(hass, request, {}), (None, "unknown"))
        self.assertEqual(origin_context(hass, request, {"default_area": "area_sala"}), ("area_sala", "default"))
        request.device_id = "device_fixture"
        hass.device_registry.async_get = lambda did: SimpleNamespace(area_id="area_quarto") if did == "device_fixture" else None
        self.assertEqual(origin_context(hass, request, {}), ("area_quarto", "device"))
        request.satellite_id = "light.sala_0"
        self.assertEqual(origin_context(hass, request, {}), ("area_quarto", "device"))


class ExecutorPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_available_set_accepts_offline_member_in_snapshot(self):
        hass = house()
        hass.states._states["light.sala_1"].state = "unavailable"
        op = operation(targets=["light_sala_0"], params={"scope": "bulk_area", "area": "area_sala", "category": "light"})
        op["evidence"] = ["bulk_area", "complete_spatial_set"]
        result = await ContextualRuntime(hass, Client(plan(op))).process(input_())
        self.assertEqual(result.code, "success", result)
        self.assertEqual(called(hass), {"light.sala_0"})

    async def test_ha_2025_3_response_constructor_without_followup_field(self):
        from test_contextual_identity import entity
        from custom_components.local_nlu.runtime import RuntimeResult
        @dataclass(slots=True)
        class OldResult:
            response: object
            conversation_id: str | None = None
        async def process(_):
            return RuntimeResult(code="missing_slot", response_text="Em qual cômodo?", continue_conversation=True)
        with patch.dict(sys.modules, {"homeassistant.core": SimpleNamespace(HomeAssistant=object)}):
            agent = entity(SimpleNamespace(async_process=process))
        module = agent.async_process.__globals__["conversation"]
        with patch.object(module, "ConversationResult", OldResult):
            result = await agent.async_process(input_("luz"))
        self.assertEqual(result.response.speech, "Em qual cômodo?")
        self.assertEqual(result.conversation_id, "fixture")

    async def test_incomplete_spatial_target_set_cannot_execute(self):
        hass = house()
        op = operation(targets=["light_sala_0"], params={"scope": "bulk_area", "area": "area_sala", "category": "light"})
        op["evidence"] = ["bulk_area", "complete_spatial_set"]
        result = await ContextualRuntime(hass, Client(plan(op))).process(input_())
        self.assertEqual(result.reason, "bulk_target_set_changed", result)
        self.assertFalse(hass.services.calls)

    async def test_forged_implicit_plan_cannot_bypass_origin_or_category(self):
        for params, targets in [({"area": "area_sala", "scope": "bulk_area"}, ["light_sala_0"]), ({}, ["light_sala_0"]), ({"area": "area_sala", "scope": "bulk_area"}, ["fan_sala"])]:
            hass = house()
            op = operation(targets=targets, params=params, intent="light.implicit_on")
            result = await ContextualRuntime(hass, Client(plan(op))).process(input_("luz"))
            self.assertEqual(result.reason, "implicit_origin_policy", result)
            self.assertFalse(hass.services.calls)

    async def test_forged_bulk_area_category_and_global_activation_refused(self):
        for params in ({"scope": "bulk_area", "area": "area_quarto"}, {"scope": "bulk_area", "area": "area_sala", "category": "fan"}, {"scope": "bulk_global"}):
            hass = house()
            result = await ContextualRuntime(hass, Client(plan(operation(targets=["light_sala_0"], params=params)))).process(input_())
            self.assertEqual(result.code, "denied", result)
            self.assertFalse(hass.services.calls)

    async def test_scope_omission_does_not_bypass_multi_target_exclusion(self):
        hass = house()
        options = {"excluded_from_bulk_actions": ["light.sala_1"]}
        op = operation(targets=["light_sala_0", "light_sala_1"])
        result = await ContextualRuntime(hass, Client(plan(op)), lambda: options).process(input_())
        self.assertEqual(result.reason, "bulk_policy", result)
        self.assertFalse(hass.services.calls)


@unittest.skipUnless(os.environ.get("ARANDU_NLU_BINARY"), "requires real Rust HTTP binary")
class SpatialHttpTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.EndToEndTests.setUpClass()
        cls.endpoint = fixtures.EndToEndTests.endpoint

    @classmethod
    def tearDownClass(cls):
        fixtures.EndToEndTests.tearDownClass()

    async def run_command(self, text, hass=None, options=None, satellite="sala", client=None):
        hass = hass or house()
        request = input_(text, satellite_id="assist_satellite." + satellite if satellite else None)
        result = await ContextualRuntime(hass, client or HttpClient(self.endpoint), lambda: options or {}).process(request)
        return hass, result

    async def test_explicit_room_activation_and_deactivation_matrix(self):
        for verb, service in (("liga", "turn_on"), ("desliga", "turn_off")):
            for target in ("a sala", "todos os dispositivos da sala", "tudo na sala"):
                with self.subTest(verb=verb, target=target):
                    hass, result = await self.run_command(f"{verb} {target}", satellite="cozinha")
                    self.assertEqual(result.code, "success", result)
                    self.assertEqual(called(hass), {"light.sala_0", "light.sala_1", "fan.sala"})
                    self.assertEqual({call["service"] for call in hass.services.calls}, {service})

    async def test_category_matrix_and_explicit_area_override(self):
        for verb, service in (("liga", "turn_on"), ("desliga", "turn_off"), ("acende", "turn_on"), ("apaga", "turn_off")):
            for target, area in (("todas as luzes da sala", "sala"), ("as luzes", "cozinha"), ("as luzes do quarto", "quarto")):
                with self.subTest(verb=verb, target=target):
                    hass, result = await self.run_command(f"{verb} {target}", satellite="sala" if area == "quarto" else "cozinha")
                    self.assertEqual(result.code, "success", result)
                    self.assertEqual(called(hass), {f"light.{area}_0", f"light.{area}_1"})
                    self.assertEqual({call["service"] for call in hass.services.calls}, {service})

    async def test_implicit_allowlist_each_satellite_and_fan(self):
        for word, domain in (("Luz", "light"), ("Luzes", "light"), ("Iluminação", "light"), ("Lâmpadas", "light"), ("Ventilador", "fan"), ("Ventiladores", "fan")):
            for room in ("sala", "cozinha"):
                with self.subTest(word=word, room=room):
                    hass, result = await self.run_command(word, satellite=room)
                    self.assertEqual(result.code, "success", result)
                    expected = {f"light.{room}_0", f"light.{room}_1"} if domain == "light" else {f"fan.{room}"}
                    self.assertEqual(called(hass), expected)
                    self.assertTrue(all(call["domain"] == domain and call["service"] == "turn_on" for call in hass.services.calls))
        hass = house()
        runtime = ContextualRuntime(hass, HttpClient(self.endpoint))
        for room in ("sala", "cozinha"):
            hass.services.calls.clear()
            result = await runtime.process(input_("luz", satellite_id="assist_satellite." + room))
            self.assertEqual(result.code, "success", result)
            self.assertEqual(called(hass), {f"light.{room}_0", f"light.{room}_1"})

    async def test_unknown_unassigned_disabled_and_default_origins_abstain(self):
        for satellite in (None, "missing", "sala"):
            for options in ({}, {"default_area": "area_quarto"}):
                hass = house()
                if satellite == "sala":
                    hass.entity_registry.entities["assist_satellite.sala"].area_id = None
                hass, result = await self.run_command("luz", hass, options, satellite)
                self.assertEqual((result.code, result.response_text), ("missing_slot", "Em qual cômodo?"), result)
                self.assertFalse(hass.services.calls)
        hass = house()
        hass.entity_registry.entities["assist_satellite.sala"].disabled_by = "user"
        hass, result = await self.run_command("luz", hass)
        self.assertEqual(result.code, "missing_slot", result)
        self.assertFalse(hass.services.calls)

    async def test_registered_device_without_satellite_is_trusted(self):
        hass = house()
        hass.device_registry.async_get = lambda did: SimpleNamespace(area_id="area_cozinha") if did == "voice_fixture" else None
        @dataclass(slots=True)
        class DeviceConversationInput:
            text: str
            context: object
            conversation_id: str | None
            device_id: str | None
            language: str
            agent_id: str
        request = DeviceConversationInput("luz", SimpleNamespace(user_id="user"), "fixture", "voice_fixture", "pt-BR", "conversation.arandu")
        result = await ContextualRuntime(hass, HttpClient(self.endpoint)).process(request)
        self.assertEqual(result.code, "success", result)
        self.assertEqual(called(hass), {"light.cozinha_0", "light.cozinha_1"})

    async def test_configured_wake_word_and_explicit_clarification_reach_services(self):
        hass, result = await self.run_command("Alexa, luz", options={"wake_words": ["Alexa"]})
        self.assertEqual(result.code, "success", result)
        self.assertEqual(called(hass), {"light.sala_0", "light.sala_1"})
        hass = house()
        runtime = ContextualRuntime(hass, HttpClient(self.endpoint))
        first = await runtime.process(input_("luz"))
        self.assertEqual(first.response_text, "Em qual cômodo?", first)
        self.assertFalse(hass.services.calls)
        second = await runtime.process(input_("sala"))
        self.assertEqual(second.code, "success", second)
        self.assertEqual(called(hass), {"light.sala_0", "light.sala_1"})

    async def test_arbitrary_words_and_global_on_do_not_execute(self):
        for text in ("fechadura", "portão", "alarme", "câmera", "cafeteira", "qualquer", "Alexa luz", "liga tudo"):
            hass, result = await self.run_command(text, satellite=None)
            self.assertNotEqual(result.code, "success", (text, result))
            self.assertFalse(hass.services.calls, text)

    async def test_exclusions_critical_groups_bulk_domains_and_individual_control(self):
        for text in ("liga a sala", "desliga a sala", "liga todas as luzes da sala", "desliga todas as luzes da sala", "luz"):
            for exclusion in ({"excluded_from_bulk_actions": ["light.sala_1"]}, {"entity_preferences": {"light.sala_1": {"critical": True}}}, {"entity_preferences": {"light.sala_1": {"bulk_eligible": False}}}, {"entity_preferences": {"light.sala_1": {"excluded_from_bulk_actions": True}}}, {"excluded_from_bulk_actions": ["group.fixture"]}):
                hass = house()
                add(hass, "group", "group.fixture", "Grupo técnico", {"entity_id": ["light.sala_1"]})
                hass, result = await self.run_command(text, hass, exclusion)
                self.assertEqual(result.code, "success", (text, result))
                self.assertNotIn("light.sala_1", called(hass))
        hass, result = await self.run_command("liga a sala", options={"bulk_domains": ["light"]})
        self.assertEqual(called(hass), {"light.sala_0", "light.sala_1"})
        hass, result = await self.run_command("liga light.sala_1", options={"excluded_from_bulk_actions": ["light.sala_1"]})
        self.assertEqual(result.code, "success", result)
        self.assertEqual(called(hass), {"light.sala_1"})

    async def test_missing_capability_is_respected(self):
        hass = house()
        hass.states._states["fan.sala"].attributes["supported_features"] = 16
        hass, result = await self.run_command("liga a sala", hass)
        self.assertEqual(result.code, "success", result)
        self.assertEqual(called(hass), {"light.sala_0", "light.sala_1"})

    async def test_six_domains_and_sensitive_domains_remain_separate(self):
        for verb, service in (("liga", "turn_on"), ("desliga", "turn_off")):
            hass = house()
            for domain, features in (("switch", 0), ("climate", 384), ("media_player", 384), ("humidifier", 0)):
                add(hass, domain, domain + ".fixture", "Dispositivo técnico " + domain, {"supported_features": features})
                hass.services.available.update({(domain, "turn_on"), (domain, "turn_off")})
            for domain in ("lock", "cover", "alarm_control_panel", "camera", "automation", "scene", "script", "sensor"):
                add(hass, domain, domain + ".fixture", "Proteção técnica " + domain, {"supported_features": 1023})
                hass.services.available.update({(domain, "turn_on"), (domain, "turn_off")})
            hass, result = await self.run_command(verb + " a sala", hass)
            self.assertEqual(result.code, "success", result)
            self.assertEqual({call["domain"] for call in hass.services.calls}, {"light", "switch", "fan", "climate", "media_player", "humidifier"})
            self.assertEqual({call["service"] for call in hass.services.calls}, {service})

    async def test_unavailable_other_category_and_excluded_member_do_not_block_lights(self):
        hass = house()
        hass.states._states["fan.sala"].state = "unavailable"
        hass, result = await self.run_command("luz", hass)
        self.assertEqual(result.code, "success", result)
        hass = house()
        hass.states._states["light.sala_1"].state = "unavailable"
        hass, result = await self.run_command("liga todas as luzes da sala", hass, {"excluded_from_bulk_actions": ["light.sala_1"]})
        self.assertEqual(result.code, "success", result)
        self.assertEqual(called(hass), {"light.sala_0"})

    async def test_origin_and_permission_revocation_after_first_effect_stop_with_explicit_failure(self):
        for revoke in ("origin", "permission"):
            hass = house()
            hass.auth.user = _User({(eid, policy) for eid in hass.entity_registry.entities for policy in ("read", "control")})
            def after():
                if revoke == "origin": hass.entity_registry.entities["assist_satellite.sala"].area_id = "area_cozinha"
                else: hass.auth.user.permissions.allowed.remove(("light.sala_1", "control"))
            hass.services.after_call = after
            hass, result = await self.run_command("luz", hass)
            self.assertEqual(result.code, "partial_failure", result)
            self.assertEqual(len(hass.services.calls), 1)
            self.assertIsNotNone(result.reason)

    async def test_registered_service_is_required(self):
        hass = house()
        hass.services.available.remove(("fan", "turn_on"))
        hass, result = await self.run_command("liga a sala", hass)
        self.assertEqual(called(hass), {"light.sala_0", "light.sala_1"})

    async def test_bulk_skips_offline_members_and_executes_available_targets(self):
        for text in ("liga a sala", "desliga a sala", "liga todas as luzes da sala", "luz"):
            for state in ("unavailable", "unknown", None):
                hass = house()
                if state is None:
                    hass.states._states.pop("light.sala_1")
                else:
                    hass.states._states["light.sala_1"].state = state
                hass, result = await self.run_command(text, hass)
                self.assertEqual(result.code, "success", (text, state, result))
                expected = {"light.sala_0", "fan.sala"} if text in ("liga a sala", "desliga a sala") else {"light.sala_0"}
                self.assertEqual(called(hass), expected)

    async def test_all_offline_and_explicit_offline_target_have_no_effect(self):
        hass = house()
        for eid in ("light.sala_0", "light.sala_1", "fan.sala"):
            hass.states._states[eid].state = "unavailable"
        hass, result = await self.run_command("desliga a sala", hass)
        self.assertNotEqual(result.code, "success", result)
        self.assertFalse(hass.services.calls)
        hass = house()
        hass.states._states["light.sala_1"].state = "unavailable"
        hass, result = await self.run_command("desliga light.sala_1", hass)
        self.assertNotEqual(result.code, "success", result)
        self.assertFalse(hass.services.calls)

    async def test_missing_named_target_echoes_mention_without_effect_or_followup(self):
        for label in ("luz do módulo inexistente", "luz inexistente"):
            hass, result = await self.run_command("liga a " + label)
            self.assertEqual(result.reason, "target_not_found", result)
            self.assertEqual(result.response_text, f'Não encontrei um dispositivo chamado “{label}”.')
            self.assertFalse(result.continue_conversation)
            self.assertFalse(hass.services.calls)
        # Known names with a missing capability remain a different failure.
        hass = house()
        hass.services.available.remove(("light", "turn_on"))
        hass, result = await self.run_command("liga light.sala_0", hass)
        self.assertEqual(result.reason, "no_compatible_capability", result)
        self.assertFalse(hass.services.calls)

    async def test_limit_still_refuses_complete_operation(self):
        hass = house()
        for n in range(31):
            add(hass, f"extra_{n:02}", f"light.extra_{n}", f"Extra {n}", {})
        hass, result = await self.run_command("liga a sala", hass)
        self.assertEqual(result.reason, "target_limit", result)
        self.assertFalse(hass.services.calls)

    async def test_unknown_homonymous_room_and_scene_name_collision(self):
        hass = house()
        hass.area_registry.areas.pop("area_sala")
        for entry in hass.entity_registry.entities.values():
            if entry.area_id == "area_sala": entry.area_id = None
        hass, result = await self.run_command("liga a sala", hass)
        self.assertNotEqual(result.code, "success", result)
        self.assertFalse(hass.services.calls)
        hass, result = await self.run_command("liga as luzes da biblioteca")
        self.assertNotEqual(result.code, "success", result)
        self.assertFalse(hass.services.calls)
        hass = house()
        hass.area_registry.areas["area_quarto"].aliases.add("Sala")
        hass, result = await self.run_command("liga a sala", hass)
        self.assertEqual(result.reason, "ambiguous_area", result)
        self.assertFalse(hass.services.calls)
        for domain in ("scene", "script"):
            hass = house()
            add(hass, "collision", domain + ".sala", "Sala", {})
            hass.services.available.add((domain, "turn_on"))
            for phrase in ("liga a sala", "ligar sala", "por favor liga a sala", "poderia ligar a sala", "ativa a sala", "desativar a sala"):
                hass.services.calls.clear()
                hass, result = await self.run_command(phrase, hass)
                self.assertEqual(result.code, "success", (phrase, result))
                self.assertEqual(called(hass), {"light.sala_0", "light.sala_1", "fan.sala"})

    async def test_catalog_options_permission_and_origin_changes_abort_before_effect(self):
        for change in ("member", "options", "permission", "exposure", "origin", "origin_provenance", "offline", "online"):
            hass, options = house(), {}
            if change == "online": hass.states._states["light.sala_1"].state = "unavailable"
            if change == "origin_provenance": options["default_area"] = "area_sala"
            if change == "permission":
                hass.auth.user = _User({(eid, policy) for eid in hass.entity_registry.entities for policy in ("read", "control")})
            class ChangingClient(HttpClient):
                async def async_interpret_v4(client, payload):
                    response = await super().async_interpret_v4(payload)
                    if change == "member": add(hass, "new", "light.new", "Nova", {})
                    elif change == "options": options["excluded_from_bulk_actions"] = ["light.sala_0"]
                    elif change == "permission": hass.auth.user.permissions.allowed.remove(("light.sala_1", "control"))
                    elif change == "exposure": hass.exposed.remove("light.sala_1")
                    elif change == "origin": hass.entity_registry.entities["assist_satellite.sala"].area_id = "area_cozinha"
                    elif change == "offline": hass.states._states["light.sala_1"].state = "unavailable"
                    elif change == "online": hass.states._states["light.sala_1"].state = "off"
                    else: hass.entity_registry.entities.pop("assist_satellite.sala")
                    return response
            text = "liga a sala" if change in ("member", "permission") else "luz"
            hass, result = await self.run_command(text, hass, options, client=ChangingClient(self.endpoint))
            self.assertNotEqual(result.code, "success", (change, result))
            self.assertFalse(hass.services.calls, change)

    async def test_users_are_isolated_and_repeated_activation_is_idempotent(self):
        hass = house()
        hass.auth.user = _User({("light.sala_0", "read"), ("light.sala_0", "control")})
        runtime = ContextualRuntime(hass, HttpClient(self.endpoint))
        request = input_("luz", satellite_id="assist_satellite.sala")
        first = await runtime.process(request)
        self.assertEqual(first.code, "success", first)
        self.assertEqual(called(hass), {"light.sala_0"})
        hass.states._states["light.sala_0"].state = "on"
        second = await runtime.process(request)
        self.assertEqual(second.code, "cancelled", second)
        self.assertTrue(all(call["service"] == "turn_on" for call in hass.services.calls))
        hass.auth.user = _User({("light.cozinha_0", "read"), ("light.cozinha_0", "control")})
        other = await runtime.process(input_("luz", conversation_id="other", user_id="other", satellite_id="assist_satellite.cozinha"))
        self.assertEqual(other.code, "success", other)
        self.assertEqual(called(hass), {"light.sala_0", "light.cozinha_0"})

    async def test_here_compounds_and_floor_share_bulk_policy(self):
        for text, service in (("acende aqui", "turn_on"), ("apaga aqui", "turn_off"), ("acender as luzes desse cômodo", "turn_on"), ("apagar as luzes onde estou", "turn_off")):
            hass, result = await self.run_command(text)
            self.assertEqual(result.code, "success", (text, result))
            self.assertEqual(called(hass), {"light.sala_0", "light.sala_1"})
            self.assertEqual({call["service"] for call in hass.services.calls}, {service})
        hass, result = await self.run_command("liga a sala e desliga as luzes da cozinha")
        self.assertEqual(result.code, "success", result)
        self.assertEqual(len(hass.services.calls), 5)
        hass, result = await self.run_command("liga a sala e desliga as luzes da sala")
        self.assertEqual(result.reason, "contradictory_plan", result)
        self.assertFalse(hass.services.calls)
        hass = house()
        for area in ("area_sala", "area_cozinha"):
            hass.area_registry.areas[area].floor_id = "floor_fixture"
        floor = SimpleNamespace(name="Térreo", aliases=set())
        registry = SimpleNamespace(floors={"floor_fixture": floor})
        floors = SimpleNamespace(async_get=lambda _: registry)
        with patch.dict(sys.modules, {"homeassistant.helpers.floor_registry": floors}):
            for verb in ("liga", "desliga"):
                hass.services.calls.clear()
                hass, result = await self.run_command(f"{verb} as luzes do térreo", hass, {"excluded_from_bulk_actions": ["light.sala_1"]})
                self.assertEqual(result.code, "success", result)
                self.assertEqual(called(hass), {"light.sala_0", "light.cozinha_0", "light.cozinha_1"})
