"""FIXTURE_TECNICA: routing, thresholds, real executor boundaries; no neural accuracy claims."""
from __future__ import annotations

import asyncio
import copy
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

try:
    from test_contextual import Client, hass_, operation, plan, add
except ImportError:
    from tests.mlp.test_contextual import Client, hass_, operation, plan, add
from custom_components.local_nlu.ai_lab_core import VERSION, benchmark, candidates, decision_policy, digest, pipeline, validate_proposal
from custom_components.local_nlu.ai_lab import Lab
from custom_components.local_nlu.contextual_catalog import build


def proposal(entity="light.sala", intent="turn_on"):
    return {"intent": intent, "target_entities": [entity], "target_area": None, "confidence": None,
            "requires_clarification": False, "clarification_question": None, "reason": "FIXTURE_TECNICA"}


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.hass = hass_()
        self.catalog = build(self.hass, self.hass.auth.user, {}).payload
        self.ranking = [{"raw_score": .8, "entity_id": "light.sala", "intent": "turn_on"},
                        {"raw_score": .3, "entity_id": "fan.ventilador", "intent": "turn_on"}]
        self.calibration = {"version": VERSION, "model": "fixture", "catalog": digest(self.catalog),
                            "validated": True, "bins": [{"n": 20, "correct": 18} for _ in range(5)],
                            "validation_bins": [{"n": 20, "correct": 18} for _ in range(5)]}

    def decide(self, **kwargs):
        return decision_policy(kwargs.get("ranking", self.ranking), "fixture", self.catalog,
                               kwargs.get("config", {}), kwargs.get("calibration", self.calibration))

    def test_uncalibrated_never_accepted(self):
        result = self.decide(calibration=None)
        self.assertEqual(result["decision_status"], "uncalibrated")
        self.assertIsNone(result["confidence"])
        self.assertIsNone(result["proposal"])

    def test_strict_threshold_and_margin(self):
        self.assertEqual(self.decide()["decision_status"], "accepted")
        for count in (0, 10):
            self.calibration["bins"][4]["correct"] = count
            self.calibration["validation_bins"][4]["correct"] = count
            self.assertEqual(self.decide()["decision_status"], "rejected")
        self.calibration["bins"][4]["correct"] = 11
        self.calibration["validation_bins"][4]["correct"] = 11
        self.assertEqual(self.decide()["decision_status"], "accepted")
        self.ranking[1]["raw_score"] = .8
        self.assertEqual(self.decide()["decision_status"], "ambiguous")

    def test_calibration_invalidated_by_model_catalog_or_insufficient_samples(self):
        for field, value in (("model", "other"), ("catalog", "stale"), ("version", "old"), ("validated", False)):
            broken = {**self.calibration, field: value}
            self.assertEqual(self.decide(calibration=broken)["decision_status"], "uncalibrated")
        self.calibration["bins"][4]["n"] = 2
        self.assertEqual(self.decide()["decision_status"], "uncalibrated")

    def test_invalid_threshold_and_nonfinite_scores(self):
        for config in ({"threshold": .49}, {"threshold": float("nan")}, {"min_margin": 0}, {"threshold": True}):
            with self.assertRaises(ValueError):
                self.decide(config=config)
        with self.assertRaises(ValueError):
            self.decide(ranking=[{**self.ranking[0], "raw_score": float("inf")}])

    def test_no_match_is_explicit(self):
        self.assertEqual(self.decide(ranking=[])["decision_status"], "rejected")

    def test_every_eligible_entity_participates(self):
        for i in range(60):
            add(self.hass, f"fixture_{i}", f"light.fixture_{i}", f"Fixture {i}", {})
        catalog = build(self.hass, self.hass.auth.user, {}).payload
        result = candidates(catalog)
        ids = {r["entity_id"] for r in result}
        self.assertTrue(all(f"light.fixture_{i}" in ids for i in range(60)))
        unavailable = next(r for r in catalog["entities"] if r["entity_id"] == "light.fixture_0")
        unavailable["attributes"]["available"] = False
        self.assertNotIn("light.fixture_0", {r["entity_id"] for r in candidates(catalog)})

    def test_schema_rejects_invented_target_capability_area_and_services(self):
        self.assertEqual(validate_proposal(proposal(), self.catalog)["intent"], "turn_on")
        bad_values = [proposal("light.inventada"), proposal(intent="unlock"),
                      {**proposal(), "service": "shell_command"}, {**proposal(), "target_area": "inventada"},
                      {**proposal(), "confidence": float("nan")}, {**proposal(), "target_entities": ["light.sala", "light.sala"]},
                      proposal("sensor.temperatura", "turn_on")]
        bad_values.extend([{**proposal(), "requires_clarification": True}, {**proposal(), "intent": None, "target_entities": []}])
        for value in bad_values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_proposal(value, self.catalog)
        with self.assertRaises(ValueError):
            validate_proposal(proposal(), self.catalog, {"fan.ventilador"})

    def test_unmeasured_metrics_are_null(self):
        self.assertIsNone(benchmark([])["accuracy"])
        self.assertIsNone(benchmark([])["latency_ms"]["p95"])

    def test_labelled_rejections_count_and_read_errors_are_not_activations(self):
        result = {"total_ms": 1, "operations_validated": False, "stages": {"llm": {"status": "skipped"}, "decision": {"output": {}}}}
        actual = {"intent": None, "target_entities": [], "requires_clarification": True}
        metrics = benchmark([{"expected": actual, "actual": actual, "result": result}])
        self.assertEqual(metrics["accuracy"], 1)
        self.assertEqual(metrics["rejection_rate"], 1)
        wrong_read = {"intent": "query", "target_entities": ["sensor.wrong"], "requires_clarification": False}
        metrics = benchmark([{"expected": {"intent": "query", "target_entities": ["sensor.fixture"]}, "actual": wrong_read, "result": {**result, "operations_validated": True}}])
        self.assertEqual(metrics["false_activations"], 0)
        self.assertEqual(metrics["device_errors"], 1)


class RoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_nlu_resolution_skips_both_models(self):
        async def nlu(_): return plan(operation())
        async def forbidden(_): self.fail("unnecessary fallback")
        result = await pipeline("FIXTURE_TECNICA", nlu, forbidden, forbidden)
        self.assertEqual(result["source"], "nlu")
        self.assertEqual(result["stages"]["decision"]["status"], "skipped")
        self.assertEqual(result["stages"]["llm"]["status"], "skipped")

    async def test_accepted_decision_skips_llm(self):
        async def nlu(_): return {"status": "no_match"}
        async def de(_): return {"decision_status": "accepted", "proposal": proposal()}
        async def forbidden(_): self.fail("unnecessary LLM")
        result = await pipeline("FIXTURE_TECNICA", nlu, de, forbidden)
        self.assertEqual(result["source"], "decision")

    async def test_uncalibrated_ambiguous_rejected_routes_to_llm(self):
        async def nlu(_): return {"status": "clarification"}
        for status in ("uncalibrated", "ambiguous", "rejected"):
            async def de(_): return {"decision_status": status, "proposal": None}
            async def llm(data):
                self.assertEqual(data["decision"]["decision_status"], status)
                return proposal()
            result = await pipeline("FIXTURE_TECNICA", nlu, de, llm)
            self.assertTrue(result["requires_review"])
            self.assertEqual(result["source"], "llm")

    async def test_timeout_and_bad_json_end_in_clarification(self):
        async def slow(_): await asyncio.sleep(.1)
        async def invalid(_): raise ValueError("JSON inválido")
        result = await pipeline("FIXTURE_TECNICA", slow, invalid, invalid, timeout=.001)
        self.assertEqual(result["stages"]["nlu"]["error"], "TimeoutError")
        self.assertIsNone(result["proposal"])
        self.assertTrue(result["clarification_question"])

    async def test_isolated_mode_does_not_call_other_components(self):
        async def nlu(_): return {"status": "no_match"}
        async def forbidden(_): self.fail("isolated NLU must not call models")
        result = await pipeline("FIXTURE_TECNICA", nlu, forbidden, forbidden, mode="nlu")
        self.assertIsNone(result["proposal"])

    async def test_llm_mode_exposes_retrieval_and_does_not_short_circuit(self):
        async def forbidden(_): self.fail("isolated LLM must skip NLU")
        async def de(_): return {"decision_status": "accepted", "proposal": proposal(), "ranking": []}
        async def llm(_): return {**proposal(), "intent": "conversation", "target_entities": []}
        result = await pipeline("FIXTURE_TECNICA", forbidden, de, llm, mode="llm")
        self.assertEqual(result["source"], "llm")
        self.assertEqual(result["stages"]["decision"]["status"], "skipped")


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass = hass_()
        self.user = self.hass.auth.user
        self.user.id = "user"
        self.client = Client(plan(operation()))
        entry = SimpleNamespace(entry_id="fixture", options={}, runtime_data=SimpleNamespace(_client=self.client))
        self.hass.config_entries = SimpleNamespace(async_entries=lambda domain: [entry])
        self.entry = entry
        self.lab = Lab(self.hass)
        module = ModuleType("homeassistant.core")
        module.Context = lambda user_id: SimpleNamespace(user_id=user_id)
        self.modules = patch.dict(sys.modules, {"homeassistant.core": module})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    async def call(self, command="run", **kwargs):
        return await self.lab._handle(self.user, {"command": command, "text": "FIXTURE_TECNICA", **kwargs}, None, "http://127.0.0.1:11556")

    async def test_dry_run_validates_but_never_calls_services(self):
        result = await self.call()
        self.assertTrue(result["operations_validated"])
        self.assertTrue(result["dry_run"])
        self.assertEqual(self.hass.services.calls, [])
        self.assertIn("execution_token", result)

    async def test_execution_reuses_production_boundary_and_consumes_token(self):
        result = await self.call()
        executed = await self.call("execute", token=result["execution_token"], confirm=True)
        self.assertEqual(executed["code"], "success")
        self.assertEqual(len(self.hass.services.calls), 1)
        with self.assertRaises(ValueError):
            await self.call("execute", token=result["execution_token"], confirm=True)

    async def test_missing_confirmation_no_effects(self):
        result = await self.call()
        with self.assertRaises(ValueError):
            await self.call("execute", token=result["execution_token"])
        self.assertEqual(self.hass.services.calls, [])

    async def test_stale_catalog_and_changed_options_reject(self):
        result = await self.call()
        self.entry.options = {"bulk_domains": ["light"]}
        with self.assertRaises(ValueError):
            await self.call("execute", token=result["execution_token"], confirm=True)
        self.assertEqual(self.hass.services.calls, [])

    async def test_expired_and_other_user_tokens_reject(self):
        result = await self.call()
        self.lab.proposals[result["execution_token"]]["expires"] = 0
        with self.assertRaises(ValueError):
            await self.call("execute", token=result["execution_token"], confirm=True)
        result = await self.call()
        self.user.id = "different"
        with self.assertRaises(ValueError):
            await self.call("execute", token=result["execution_token"], confirm=True)
        self.assertEqual(self.hass.services.calls, [])

    async def test_invalid_nlu_plan_never_creates_token(self):
        self.client.response = plan(operation(targets=["inventado"]))
        result = await self.call()
        self.assertFalse(result["operations_validated"])
        self.assertNotIn("execution_token", result)
        self.assertEqual(self.hass.services.calls, [])

    async def test_admin_boundary_before_network(self):
        self.user.is_admin = False
        with self.assertRaises(ValueError):
            await self.lab.handle(self.user, {"command": "run"})

    async def test_malformed_worker_acceptance_never_executes(self):
        self.client.response = {"version": 4, "status": "no_match"}
        malformed = {"decision_status": "accepted", "confidence": None, "margin": 1, "proposal": proposal()}
        with patch("custom_components.local_nlu.ai_lab.local_request", AsyncMock(return_value=malformed)):
            result = await self.call()
        self.assertFalse(result["operations_validated"])
        self.assertNotIn("execution_token", result)
        self.assertEqual(self.hass.services.calls, [])
