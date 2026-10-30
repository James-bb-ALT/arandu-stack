"""FIXTURE_TECNICA: identity-less Assist input, authorization and delivery."""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from types import ModuleType
import importlib
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

try:
    import test_contextual as fixtures
    from test_contextual import Client, HttpClient, add, hass_, operation, plan
    from test_catalog_runtime import _User
except ImportError:
    from tests.mlp import test_contextual as fixtures
    from tests.mlp.test_contextual import Client, HttpClient, add, hass_, operation, plan
    from tests.mlp.test_catalog_runtime import _User
from custom_components.local_nlu.contextual_runtime import ContextualRuntime
from custom_components.local_nlu.runtime import LocalNluRuntime
from custom_components.local_nlu.identity import IdentityError, resolve_request_identity
from custom_components.local_nlu.diagnostics import async_get_config_entry_diagnostics


@dataclass(slots=True)
class ConversationInput:
    """HA 2026.9.4 ConversationInput constructor contract, simulated HA context."""
    text: str
    context: object
    conversation_id: str | None
    device_id: str | None
    satellite_id: str | None
    language: str
    agent_id: str


def voice_input(text="Desliga os ventiladores de Ana", user_id=None):
    return ConversationInput(text, SimpleNamespace(user_id=user_id, id="fixture-context"),
                             "fixture", None, "assist_satellite.fixture", "pt-BR", "conversation.arandu_nlu")


class Context:
    def __init__(self, user_id=None, parent_id=None, id=None):
        self.user_id, self.parent_id, self.id = user_id, parent_id, id or "fixture-effective-context"


def entity(runtime):
    """Actual Arandu entity/runtime, only HA response/platform classes simulated."""
    conversation = ModuleType("homeassistant.components.conversation")
    conversation.ConversationEntity = type("Entity", (), {})
    conversation.AbstractConversationAgent = type("Agent", (), {})
    conversation.ConversationEntityFeature = SimpleNamespace(CONTROL=1)
    conversation.ConversationResult = lambda **kw: SimpleNamespace(**kw)
    intent = ModuleType("homeassistant.helpers.intent")
    class Response:
        def __init__(self, **kw):
            self.error = None
        def async_set_speech(self, text):
            self.speech = text
        def async_set_error(self, error, text):
            self.error, self.speech = error, text
    intent.IntentResponse = Response
    intent.IntentResponseType = SimpleNamespace(QUERY_ANSWER="query")
    intent.IntentResponseErrorCode = SimpleNamespace(UNKNOWN="unknown", NO_VALID_TARGETS="target", NO_INTENT_MATCH="no_match")
    with patch.dict(sys.modules, {"homeassistant.components.conversation": conversation, "homeassistant.helpers.intent": intent,
                                 "homeassistant.config_entries": SimpleNamespace(ConfigEntry=object),
                                 "homeassistant.helpers.entity_platform": SimpleNamespace(AddConfigEntryEntitiesCallback=object)}):
        module = importlib.reload(importlib.import_module("custom_components.local_nlu.conversation"))
        agent = module.LocalNluConversationEntity(SimpleNamespace(runtime_data=runtime, entry_id="fixture-entry", options={}, data={}))
        agent.hass = getattr(runtime, "_hass", None)
        return agent


class IdentityFixture(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.core = patch.dict(sys.modules, {"homeassistant.core": SimpleNamespace(Context=Context, HomeAssistant=object)})
        self.core.start()
        self.addCleanup(self.core.stop)


class IdentityRegressionTests(IdentityFixture):
    async def test_no_identity_or_configuration_denies_before_rust_and_ha(self):
        hass = hass_()
        client = Client(plan(operation("turn_off", ["reg_fan"])))
        result = await ContextualRuntime(hass, client).process(voice_input())
        self.assertEqual((result.code, result.reason), ("denied", "missing_user"))
        self.assertEqual(len(client.requests), 0)
        self.assertEqual(len(client.catalogs), 0)
        self.assertEqual(len(hass.services.calls), 0)

    async def test_explicit_fallback_reaches_v4_and_authorized_service(self):
        hass = hass_()
        client = Client(plan(operation("turn_off", ["reg_fan"])))
        result = await ContextualRuntime(hass, client, lambda: {"fallback_user_id": "fixture-user"}).process(voice_input())
        self.assertEqual(result.code, "success", result)
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(len(hass.services.calls), 1)
        self.assertEqual(hass.services.calls[0]["target"]["entity_id"], ["fan.ventilador"])

    async def test_missing_identity_has_specific_speech_through_entity(self):
        hass, client = hass_(), Client(plan(operation()))
        runtime = LocalNluRuntime(hass, client, contextual_enabled=lambda: True)
        response = await entity(runtime).async_process(voice_input())
        self.assertIn("ainda não está associado a um usuário", response.response.speech)
        self.assertEqual(runtime._contextual.last_outcome, {"code": "denied", "reason": "missing_user", "stage": "authorization"})
        self.assertFalse(client.requests or hass.services.calls)

    async def test_authorized_non_admin_fallback_service_uses_effective_context(self):
        hass = hass_()
        hass.auth.user = _User({("fan.ventilador", "read"), ("fan.ventilador", "control")})
        request = voice_input()
        request.context = Context(id="fixture-original")
        runtime = LocalNluRuntime(hass, Client(plan(operation("turn_off", ["reg_fan"]))),
                                  contextual_enabled=lambda: True, contextual_options=lambda: {"fallback_user_id": "fixture-user"})
        result = await entity(runtime).async_process(request)
        self.assertIsNone(result.response.error)
        self.assertEqual(result.response.speech, "Pronto.")
        context = hass.services.calls[0]["context"]
        self.assertEqual((context.user_id, context.parent_id), ("fixture-user", "fixture-original"))
        self.assertIsNone(request.context.user_id)
        self.assertFalse(hass.auth.user.is_admin)

    async def test_fallback_control_permission_is_required(self):
        hass = hass_()
        hass.auth.user = _User({("fan.ventilador", "read")})
        client = Client(plan(operation("turn_off", ["reg_fan"])))
        result = await ContextualRuntime(hass, client, lambda: {"fallback_user_id": "fixture-user"}).process(voice_input())
        self.assertEqual((result.code, result.reason), ("denied", "entity_permission"))
        self.assertEqual(len(client.requests), 1)
        self.assertFalse(hass.services.calls)

    async def test_user_revalidated_without_falling_through_after_revocation(self):
        for change in ("inactive", "missing", "permission"):
            with self.subTest(change=change):
                hass = hass_()
                user = _User({("fan.ventilador", "read"), ("fan.ventilador", "control")})
                hass.auth.user = user
                client = Client(plan(operation("turn_off", ["reg_fan"])))
                def revoke():
                    if change == "inactive": user.is_active = False
                    elif change == "missing": hass.auth.user = None
                    else: user.permissions.allowed.remove(("fan.ventilador", "control"))
                client.after = revoke
                result = await ContextualRuntime(hass, client, lambda: {"fallback_user_id": "fixture-user"}).process(voice_input())
                self.assertEqual(result.code, "denied", result)
                self.assertEqual(result.reason, "entity_permission" if change == "permission" else "inactive_or_missing_configured_user")
                self.assertFalse(hass.services.calls)

    async def test_options_change_during_inference_aborts_without_switching_user(self):
        hass, client = hass_(), Client(plan(operation("turn_off", ["reg_fan"])))
        options = {"fallback_user_id": "user-a"}
        client.after = lambda: options.update(fallback_user_id="user-b")
        result = await ContextualRuntime(hass, client, lambda: options).process(voice_input())
        self.assertEqual((result.code, result.reason), ("stale", "options_changed"))
        self.assertFalse(hass.services.calls)

    async def test_saved_options_change_and_restore_still_invalidates_inflight_request(self):
        hass, client = hass_(), Client(plan(operation("turn_off", ["reg_fan"])))
        options = {"fallback_user_id": "user-a"}
        runtime = ContextualRuntime(hass, client, lambda: options)
        def save_twice():
            options["fallback_user_id"] = "user-b"
            runtime.options_changed()
            options["fallback_user_id"] = "user-a"
            runtime.options_changed()
        client.after = save_twice
        result = await runtime.process(voice_input())
        self.assertEqual((result.code, result.reason), ("stale", "options_changed"))
        self.assertFalse(hass.services.calls)

    async def test_context_identity_wins_even_when_configured_user_has_more_permissions(self):
        hass = hass_()
        normal = _User({("fan.ventilador", "read")})
        configured = _User(set(), admin=True)
        looked_up = []
        async def lookup(user_id):
            looked_up.append(user_id)
            return normal if user_id == "context-user" else configured
        hass.auth.async_get_user = lookup
        client = Client(plan(operation("turn_off", ["reg_fan"])))
        result = await ContextualRuntime(hass, client, lambda: {"fallback_user_id": "configured-user"}).process(voice_input(user_id="context-user"))
        self.assertEqual((result.code, result.reason), ("denied", "entity_permission"))
        self.assertEqual(set(looked_up), {"context-user"})
        self.assertFalse(hass.services.calls)

    async def test_bound_origin_removed_during_inference_denies_without_fallback(self):
        hass = hass_()
        add(hass, "satellite", "assist_satellite.fixture", "Origem", {}, area=None)
        client = Client(plan(operation("turn_off", ["reg_fan"])))
        client.after = lambda: hass.entity_registry.entities.pop("assist_satellite.fixture")
        options = {"fallback_user_id": "other-user", "voice_identity_bindings": {"assist_satellite.fixture": "bound-user"}}
        result = await ContextualRuntime(hass, client, lambda: options).process(voice_input())
        self.assertEqual((result.code, result.reason), ("denied", "identity_origin_unavailable"))
        self.assertFalse(hass.services.calls)

    async def test_fallback_does_not_bypass_exposure_bulk_or_sensitive_policy(self):
        for policy in ("exposure", "bulk", "sensitive"):
            hass = hass_()
            options = {"fallback_user_id": "fixture-user"}
            client = Client(plan(operation("turn_off", ["reg_fan"])))
            if policy == "exposure": client.after = lambda: hass.exposed.remove("fan.ventilador")
            elif policy == "bulk":
                options["excluded_from_bulk_actions"] = ["fan.ventilador"]
                client.response = plan(operation("turn_off", ["reg_fan"], {"scope": "bulk_area", "area": "area_quarto"}))
            else:
                add(hass, "lock", "lock.fixture", "Porta", {})
                hass.services.available.add(("lock", "unlock"))
                client.response = plan(operation("unlock", ["lock"]))
            result = await ContextualRuntime(hass, client, lambda: options).process(voice_input())
            self.assertEqual((result.code, result.reason), ("denied", {"exposure": "not_exposed", "bulk": "bulk_policy", "sensitive": "sensitive_policy"}[policy]))
            self.assertFalse(hass.services.calls)

    async def test_saved_options_listener_invalidates_pending_and_caches(self):
        from custom_components.local_nlu import _async_options_updated
        hass, client = hass_(), Client(plan(operation("turn_off", ["reg_fan"])))
        runtime = LocalNluRuntime(hass, client, contextual_enabled=lambda: True, contextual_options=lambda: {"fallback_user_id": "fixture-user"})
        await runtime.async_process(voice_input())
        self.assertTrue(runtime._contextual.sessions)
        self.assertTrue(runtime._contextual.catalog.cache)
        client._endpoint = "http://127.0.0.1:11555"
        await _async_options_updated(hass, SimpleNamespace(runtime_data=runtime, options={"endpoint": client._endpoint}, data={}))
        self.assertFalse(runtime._contextual.sessions)
        self.assertFalse(runtime._contextual.catalog.cache)

    async def test_context_mutation_during_inference_is_not_a_user_switch(self):
        hass, client = hass_(), Client(plan(operation("turn_off", ["reg_fan"])))
        request = voice_input(user_id="user-a")
        client.after = lambda: setattr(request.context, "user_id", "user-b")
        result = await ContextualRuntime(hass, client, lambda: {"fallback_user_id": "user-b"}).process(request)
        self.assertEqual((result.code, result.reason), ("denied", "identity_changed"))
        self.assertFalse(hass.services.calls)

    async def test_permission_revoked_between_services_stops_remaining_targets(self):
        hass = hass_()
        user = _User({(entity_id, policy) for entity_id in ("light.sala", "fan.ventilador") for policy in ("read", "control")})
        hass.auth.user = user
        hass.services.after_call = lambda: user.permissions.allowed.remove(("fan.ventilador", "control"))
        client = Client(plan(operation("turn_off", ["reg_light"]), operation("turn_off", ["reg_fan"])))
        result = await ContextualRuntime(hass, client, lambda: {"fallback_user_id": "fixture-user"}).process(voice_input())
        self.assertEqual((result.code, result.reason), ("partial_failure", "entity_permission"))
        self.assertEqual(len(hass.services.calls), 1)

    async def test_identity_diagnostics_and_debug_logs_are_private(self):
        hass = hass_()
        version = json.loads(Path("custom_components/local_nlu/manifest.json").read_text())["version"]
        client = Client(plan(operation("turn_off", ["reg_fan"])))
        async def diagnostics():
            return {"service_version": version, "protocols": [1, 2, 3, 4]}
        client.async_diagnostics = diagnostics
        options = {"fallback_user_id": "private-user-identifier"}
        runtime = LocalNluRuntime(hass, client, contextual_enabled=lambda: True, contextual_options=lambda: options)
        with self.assertLogs("custom_components.local_nlu", level="DEBUG") as logs:
            await entity(runtime).async_process(voice_input())
        entry = SimpleNamespace(runtime_data=runtime, options=options)
        value = await async_get_config_entry_diagnostics(hass, entry)
        self.assertEqual(value["identity"]["last_source"], "fallback")
        self.assertTrue(value["identity"]["fallback_configured"])
        self.assertFalse(value["identity"]["origin_binding_configured"])
        self.assertTrue(value["compatible"])
        self.assertEqual(value["last_outcome"]["code"], "success")
        serialized = json.dumps(value) + " ".join(logs.output)
        for private in ("private-user-identifier", "Ana", "Desliga", "fixture-context", "assist_satellite.fixture", "fan.ventilador"):
            self.assertNotIn(private, serialized)
        self.assertIn("source=fallback authenticated=False", serialized)
        self.assertIn("route=/v4/interpret", serialized)


class IdentityPrecedenceTests(IdentityFixture):
    async def test_precedence_and_no_fallthrough_from_invalid_users(self):
        hass = hass_()
        users = {key: _User(set()) for key in ("context-user", "satellite-user", "device-user", "fallback-user")}
        looked_up = []
        async def lookup(user_id):
            looked_up.append(user_id)
            return users.get(user_id)
        hass.auth.async_get_user = lookup
        add(hass, "fixture-satellite-registry", "assist_satellite.fixture", "Origem", {})
        hass.device_registry.async_get = lambda device_id: SimpleNamespace(area_id=None) if device_id == "fixture-device" else None
        request = voice_input(user_id="context-user")
        request.device_id = "fixture-device"
        options = {"fallback_user_id": "fallback-user", "voice_identity_bindings": {request.satellite_id: "satellite-user"},
                   "device_identity_bindings": {request.device_id: "device-user"}}
        for user_id, source in (("context-user", "context"), ("satellite-user", "satellite_binding"), ("device-user", "device_binding"), ("fallback-user", "fallback")):
            identity = await resolve_request_identity(hass, request, options)
            self.assertEqual((identity.user_id, identity.source), (user_id, source))
            self.assertEqual(looked_up[-1], user_id)
            if source == "context": request.context.user_id = None
            elif source == "satellite_binding": options.pop("voice_identity_bindings")
            elif source == "device_binding": options.pop("device_identity_bindings")
        request.context.user_id = "removed-user"
        with self.assertRaises(IdentityError) as error:
            await resolve_request_identity(hass, request, options)
        self.assertEqual(error.exception.reason, "inactive_user")
        self.assertEqual(looked_up[-1], "removed-user")
        for bad in ("", 123, True):
            request.context.user_id = bad
            with self.assertRaises(IdentityError) as error:
                await resolve_request_identity(hass, request, options)
            self.assertEqual(error.exception.reason, "invalid_context_user")

    async def test_invalid_configured_user_never_uses_a_lower_precedence(self):
        hass = hass_()
        add(hass, "satellite", "assist_satellite.fixture", "Origem", {})
        async def lookup(user_id):
            return _User(set()) if user_id == "fallback-user" else None
        hass.auth.async_get_user = lookup
        for options in ({"fallback_user_id": "missing"}, {"voice_identity_bindings": {"assist_satellite.fixture": "missing"}, "fallback_user_id": "fallback-user"}):
            client = Client(plan(operation()))
            result = await ContextualRuntime(hass, client, lambda: options).process(voice_input())
            self.assertEqual((result.code, result.reason), ("denied", "inactive_or_missing_configured_user"))
            self.assertFalse(client.requests or hass.services.calls)

    async def test_binding_origin_must_exist_and_configuration_must_be_bounded(self):
        hass = hass_()
        for options in ({"voice_identity_bindings": {"assist_satellite.fixture": "user"}, "fallback_user_id": "user"},
                        {"voice_identity_bindings": {"registry-id": "user"}}, {"device_identity_bindings": []},
                        {"fallback_user_id": ["user"]}, {"fallback_user_id": "\nuser"}):
            client = Client(plan(operation()))
            result = await ContextualRuntime(hass, client, lambda: options).process(voice_input())
            self.assertEqual(result.code, "denied", result)
            self.assertFalse(client.requests or hass.services.calls)


class SpyHttpClient(HttpClient):
    def __init__(self, endpoint):
        super().__init__(endpoint)
        self.requests, self.responses = [], []

    async def async_interpret_v4(self, payload):
        self.requests.append(dict(payload))
        response = await super().async_interpret_v4(payload)
        self.responses.append(response)
        return response


def named_room(*, equal=True):
    hass = hass_()
    hass.entity_registry.entities.clear()
    hass.states._states.clear()
    hass.exposed.clear()
    hass.area_registry.areas["area_sala"].name = "Estúdio de Ana"
    add(hass, "a", "fan.a", "Ventilador de Ana", {"supported_features": 49}, "on")
    add(hass, "b", "fan.b", "Ventilador da mesa de Ana", {"supported_features": 49}, "on")
    if not equal:
        add(hass, "c", "fan.c", "Ventilador do teto", {"supported_features": 49}, "on")
    return hass


@unittest.skipUnless(os.environ.get("ARANDU_NLU_BINARY"), "requires real Rust HTTP binary")
class RealAssistIdentityTests(IdentityFixture):
    @classmethod
    def setUpClass(cls):
        fixtures.EndToEndTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        fixtures.EndToEndTests.tearDownClass.__func__(cls)

    def pipeline(self, hass, options=None):
        client = SpyHttpClient(self.endpoint)
        options = options if options is not None else {"fallback_user_id": "fixture-user"}
        runtime = LocalNluRuntime(hass, client, contextual_enabled=lambda: True, contextual_options=lambda: options)
        return entity(runtime), runtime, client

    async def test_four_families_from_identity_less_entity_to_rust_and_executor(self):
        for text, query, expected in (("Desliga os ventiladores de Ana", False, "Pronto"),
                                      ("Desliga o Estúdio de Ana", False, "Pronto"),
                                      ("qual a temperatura do Estúdio de Ana", True, "24"),
                                      ("tem alguma luz ligada", True, "Sim")):
            with self.subTest(text=text):
                hass = named_room()
                add(hass, "temp", "sensor.reading", "Temperatura ambiente", {"device_class": "temperature", "unit_of_measurement": "°C"}, "24")
                add(hass, "light", "light.reading", "Luz de leitura", {}, "on", "area_quarto")
                user = _User({(entity_id, policy) for entity_id in hass.exposed for policy in ("read", "control")})
                hass.auth.user = user
                agent, runtime, client = self.pipeline(hass)
                request = voice_input(text)
                result = await agent.async_process(request)
                self.assertIsNone(result.response.error, runtime._contextual.last_outcome)
                self.assertIn(expected, result.response.speech)
                self.assertEqual(runtime._contextual.last_outcome["code"], "query_success" if query else "success")
                self.assertEqual(len(client.requests), 1)
                self.assertEqual(client.requests[0]["text"], text)
                self.assertEqual(client.responses[0]["status"], "plan")
                self.assertNotIn("user_id", client.requests[0])
                self.assertNotIn("fallback_user_id", client.requests[0])
                self.assertIsNone(request.context.user_id)
                if query:
                    self.assertFalse(hass.services.calls)
                    self.assertEqual(result.response.response_type, "query")
                else:
                    self.assertEqual({call["target"]["entity_id"][0] for call in hass.services.calls}, {"fan.a", "fan.b"})
                    self.assertTrue(all(call["context"].user_id == "fixture-user" for call in hass.services.calls))

    async def test_fallback_clarification_yes_and_no_execute_the_correct_sets(self):
        for answer, targets in (("sim", {"fan.a", "fan.b", "fan.c"}), ("não", {"fan.a", "fan.b"})):
            with self.subTest(answer=answer):
                hass = named_room(equal=False)
                agent, runtime, client = self.pipeline(hass)
                first = await agent.async_process(voice_input())
                self.assertTrue(first.continue_conversation)
                self.assertEqual(runtime._contextual.last_outcome["reason"], "name_area_sets_differ")
                self.assertEqual(len(client.requests), 1)
                self.assertFalse(hass.services.calls)
                result = await agent.async_process(voice_input(answer))
                self.assertIsNone(result.response.error, runtime._contextual.last_outcome)
                self.assertFalse(result.continue_conversation)
                self.assertGreaterEqual(len(client.requests), 2)
                self.assertEqual({call["target"]["entity_id"][0] for call in hass.services.calls}, targets)
                self.assertTrue(all(call["context"].user_id == "fixture-user" for call in hass.services.calls))

    async def test_satellites_with_same_conversation_do_not_share_a_dialogue(self):
        hass = named_room(equal=False)
        for origin in ("assist_satellite.a", "assist_satellite.b"):
            add(hass, "registry_" + origin[-1], origin, "Origem", {}, area=None)
        users = {key: _User({(entity_id, policy) for entity_id in hass.exposed for policy in ("read", "control")}) for key in ("user-a", "user-b")}
        async def lookup(user_id): return users.get(user_id)
        hass.auth.async_get_user = lookup
        options = {"voice_identity_bindings": {"assist_satellite.a": "user-a", "assist_satellite.b": "user-b"}}
        agent, runtime, client = self.pipeline(hass, options)
        request_a = voice_input()
        request_a.satellite_id = "assist_satellite.a"
        await agent.async_process(request_a)
        request_b = voice_input("sim")
        request_b.satellite_id = "assist_satellite.b"
        result = await agent.async_process(request_b)
        self.assertIsNotNone(result.response.error)
        self.assertFalse(hass.services.calls)
        self.assertEqual({key[0] for key in runtime._contextual.sessions}, {"user-a", "user-b"})
        request_a.text = "sim"
        result = await agent.async_process(request_a)
        self.assertIsNone(result.response.error, runtime._contextual.last_outcome)
        self.assertTrue(all(call["context"].user_id == "user-a" for call in hass.services.calls))

    async def test_binding_and_fallback_changes_invalidate_pending_dialogue(self):
        for kind in ("fallback", "binding"):
            hass = named_room(equal=False)
            add(hass, "satellite", "assist_satellite.fixture", "Origem", {}, area=None)
            options = {"fallback_user_id": "user-a"}
            if kind == "binding": options["voice_identity_bindings"] = {"assist_satellite.fixture": "user-a"}
            agent, runtime, client = self.pipeline(hass, options)
            await agent.async_process(voice_input())
            self.assertTrue(next(iter(runtime._contextual.sessions.values())).selection)
            if kind == "fallback": options["fallback_user_id"] = "user-b"
            else: options["voice_identity_bindings"].clear()
            result = await agent.async_process(voice_input("sim"))
            self.assertIsNotNone(result.response.error)
            self.assertFalse(hass.services.calls)
            self.assertFalse(any(session.selection or session.confirmation or session.pending for session in runtime._contextual.sessions.values()))

    async def test_fallback_target_slot_and_sensitive_confirmation_use_same_identity(self):
        hass = named_room()
        agent, runtime, client = self.pipeline(hass)
        first = await agent.async_process(voice_input("desliga"))
        self.assertTrue(first.continue_conversation)
        result = await agent.async_process(voice_input("o Estúdio de Ana"))
        self.assertIsNone(result.response.error, runtime._contextual.last_outcome)
        self.assertEqual(len(hass.services.calls), 2)
        self.assertTrue(all(call["context"].user_id == "fixture-user" for call in hass.services.calls))

        for protected, expected in ((False, "success"), (True, "denied")):
            hass = named_room()
            add(hass, "lock", "lock.fixture", "Porta", {"code_format": "number"} if protected else {})
            hass.services.available.add(("lock", "unlock"))
            options = {"fallback_user_id": "fixture-user", "sensitive_entities": ["lock.fixture"]}
            agent, runtime, client = self.pipeline(hass, options)
            first = await agent.async_process(voice_input("destranca a Porta"))
            self.assertFalse(hass.services.calls)
            if protected:
                self.assertEqual(runtime._contextual.last_outcome["reason"], "authentication_required")
            else:
                self.assertTrue(first.continue_conversation)
                result = await agent.async_process(voice_input("sim"))
                self.assertIsNone(result.response.error, runtime._contextual.last_outcome)
                self.assertEqual(len(hass.services.calls), 1)
                self.assertEqual(hass.services.calls[0]["context"].user_id, "fixture-user")
