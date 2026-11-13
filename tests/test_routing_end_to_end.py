"""End-to-end routing, aggregated light query, and safety regression tests."""
import unittest
from types import SimpleNamespace

from custom_components.local_nlu.ai_lab_core import pipeline
from custom_components.local_nlu.intelligence import fallback
from custom_components.local_nlu.light_aggregation import (
    evaluate_aggregated_light_query,
    is_aggregated_light_query,
)


def _snapshot(lights_specs, extra_entities=()):
    entities = []
    by_id = {}
    for eid, name, area, state in lights_specs:
        row = {
            "entity_id": eid,
            "registry_id": eid.replace(".", "_"),
            "domain": "light",
            "name": name,
            "aliases": [],
            "area_id": area,
            "actions": ["turn_on", "turn_off", "query"],
            "attributes": {"available": state != "unavailable", "state": state},
        }
        entities.append(row)
        by_id[row["registry_id"]] = row
    for row in extra_entities:
        entities.append(row)
        by_id[row["registry_id"]] = row
    payload = {
        "version": 4,
        "generation": "gen-agg",
        "areas": [
            {"area_id": "sala", "names": ["Sala"]},
            {"area_id": "cozinha", "names": ["Cozinha"]},
        ],
        "entities": entities,
    }
    return SimpleNamespace(payload=payload, by_id=by_id)


class TestRoutingEndToEnd(unittest.IsolatedAsyncioTestCase):
    async def test_desliga_todas_coisas_sala_never_labels_safety_refusal(self):
        async def nlu(text):
            return {
                "status": "clarification",
                "reason": "target_not_found",
                "command": {"action": "turn_off", "area": "sala"},
            }

        async def decision(data):
            self.assertEqual(data["preserved_slots"], {"action": "turn_off", "area": "sala"})
            return {"decision_status": "abstained"}

        async def llm(data):
            return {
                "contract_version": 2,
                "kind": "clarification",
                "plan": {"type": "clarification", "question": "Quer desligar as luzes e tomadas da sala?"},
            }

        res = await pipeline("Desliga todas as coisas da sala.", nlu, decision, llm, mode="pipeline")
        self.assertNotEqual(res["stages"]["decision"].get("reason"), "nlu_safety_or_dialogue_refusal")
        self.assertNotEqual(res["stages"]["llm"].get("reason"), "nlu_safety_or_dialogue_refusal")
        self.assertEqual(res["route_diagnostic"]["route"], "RECOVERABLE")
        self.assertTrue(res["route_diagnostic"]["decision_called"])
        self.assertTrue(res["route_diagnostic"]["llm_called"])
        self.assertEqual(res["clarification_question"], "Quer desligar as luzes e tomadas da sala?")

    async def test_quantas_luzes_no_compatible_capability_not_safety_refusal(self):
        async def nlu(text):
            return {
                "status": "clarification",
                "reason": "no_compatible_capability",
                "command": {"action": "light.query", "domain": "light"},
            }

        async def decision(data):
            return {"decision_status": "abstained"}

        async def llm(data):
            return {"contract_version": 2, "kind": "clarification", "plan": {"type": "clarification", "question": "ok"}}

        res = await pipeline("quantas luzes estão ligadas na casa?", nlu, decision, llm, mode="pipeline")
        self.assertNotEqual(res["stages"]["decision"].get("reason"), "nlu_safety_or_dialogue_refusal")
        self.assertEqual(res["route_diagnostic"]["route"], "RECOVERABLE")

    async def test_safety_block_equivalence_between_pipeline_and_fallback(self):
        snap = _snapshot([("light.sala", "Luz Sala", "sala", "on")])

        async def nlu(text):
            return {"status": "no_match", "reason": "permission_denied", "command": {"action": "turn_off"}}

        called = {"decision": False, "llm": False}

        async def decision(data):
            called["decision"] = True
            return {}

        async def llm(data):
            called["llm"] = True
            return {}

        lab_res = await pipeline("Desliga a luz", nlu, decision, llm, mode="pipeline")
        self.assertEqual(lab_res["route_diagnostic"]["route"], "SAFETY_BLOCK")
        self.assertFalse(called["decision"])
        self.assertFalse(called["llm"])

        async def req(body):
            raise AssertionError("Models must not be called on SAFETY_BLOCK")

        proposed, trace = await fallback(
            "Desliga a luz",
            snap,
            {"decision_enabled": True, "llm_enabled": True},
            req,
            nlu={"status": "no_match", "reason": "permission_denied", "action": "turn_off"},
        )
        self.assertEqual(proposed, ())
        self.assertEqual(trace["diagnostic"]["route"], "SAFETY_BLOCK")
        self.assertEqual(trace["safety"]["status"], "rejected")


class TestAggregatedLightQuery(unittest.TestCase):
    def test_authorized_counts_with_unavailable_and_hidden_isolation(self):
        # User A sees 5 lights (2 on, 2 off, 1 unavailable); 2 hidden lights are not in User A's snapshot
        snap_a = _snapshot(
            [
                ("light.s1", "Luz 1", "sala", "on"),
                ("light.s2", "Luz 2", "sala", "on"),
                ("light.c1", "Luz Cozinha 1", "cozinha", "off"),
                ("light.c2", "Luz Cozinha 2", "cozinha", "off"),
                ("light.q1", "Luz Quarto", "quarto", "unavailable"),
            ]
        )
        res = evaluate_aggregated_light_query("Quantas luzes estão ligadas na casa?", snap_a)
        self.assertEqual(res["count_on"], 2)
        self.assertEqual(res["count_off"], 2)
        self.assertEqual(res["count_unavailable"], 1)
        self.assertIn("2 luzes ligadas", res["speech"])
        self.assertIn("1 indisponível", res["speech"])

        # Variants
        res_off = evaluate_aggregated_light_query("quantas luzes estão desligadas?", snap_a)
        self.assertEqual(res_off["count_off"], 2)
        self.assertIn("2 luzes desligadas", res_off["speech"])

        res_area = evaluate_aggregated_light_query("quantas luzes na cozinha estão ligadas?", snap_a)
        self.assertEqual(res_area["count_on"], 0)
        self.assertIn("0 luzes ligadas", res_area["speech"])

        res_any = evaluate_aggregated_light_query("tem alguma luz acesa?", snap_a)
        self.assertIn("Sim, há 2 luzes ligadas", res_any["speech"])

        res_list = evaluate_aggregated_light_query("quais luzes estão acesas?", snap_a)
        self.assertIn("Luz 1", res_list["speech"])
        self.assertIn("Luz 2", res_list["speech"])

        # User B sees only 1 visible ON light
        snap_b = _snapshot([("light.s1", "Luz 1", "sala", "on")])
        self.assertEqual(evaluate_aggregated_light_query("Quantas luzes estão ligadas na casa?", snap_b)["count_on"], 1)

        # User C has 0 authorized lights -> distinct message from "0 luzes ligadas"
        snap_c = _snapshot([])
        res_c = evaluate_aggregated_light_query("Quantas luzes estão ligadas na casa?", snap_c)
        self.assertIn("Não há luzes autorizadas", res_c["speech"])

    def test_does_not_hijack_other_domains(self):
        self.assertFalse(is_aggregated_light_query("Quantas câmeras estão abertas?"))
        self.assertFalse(is_aggregated_light_query("Quantas fechaduras estão abertas?"))


if __name__ == "__main__":
    unittest.main()
