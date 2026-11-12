"""Integration tests for FIX B: Conversational LLM delivery to Assist, AI Lab, and TTS."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests/mlp"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "arandu_stack/rootfs/modules/arandu_nlu/rootfs/src/tests/mlp"))

from custom_components.local_nlu.ai_lab_core import pipeline
from custom_components.local_nlu.intelligence import fallback
from custom_components.local_nlu.conversation import _render


def _sample_snapshot():
    payload = {
        "version": 4,
        "generation": "gen-1",
        "areas": [{"area_id": "sala", "names": ["Sala"]}],
        "entities": [
            {
                "entity_id": "light.sala",
                "registry_id": "light_sala",
                "domain": "light",
                "name": "Luz da Sala",
                "aliases": [],
                "area_id": "sala",
                "actions": ["turn_on", "turn_off"],
                "attributes": {"available": True},
            }
        ],
    }
    return SimpleNamespace(payload=payload, by_id={"light_sala": payload["entities"][0]})


class TestIntelligenceConversationContract(unittest.IsolatedAsyncioTestCase):
    async def test_v2_conversation_valid_response_passes_cleanly(self):
        snap = _sample_snapshot()
        options = {"decision_enabled": False, "llm_enabled": True}

        async def infer(body):
            return {
                "contract_version": 2,
                "kind": "conversation",
                "response_text": "A fotossíntese é o processo em que plantas convertem luz em energia.",
            }

        proposed, trace = await fallback("o que é fotossíntese?", snap, options, infer, {"origin_area": "sala"})
        self.assertEqual(proposed, ())
        self.assertEqual(trace["llm"]["status"], "conversation")
        self.assertEqual(
            trace["llm"]["response_text"],
            "A fotossíntese é o processo em que plantas convertem luz em energia.",
        )

    async def test_v2_conversation_empty_response_returns_explicit_error(self):
        snap = _sample_snapshot()
        options = {"decision_enabled": False, "llm_enabled": True}

        for bad_reply in (None, "", "   "):
            async def infer(body, reply=bad_reply):
                return {"contract_version": 2, "kind": "conversation", "response_text": reply}

            proposed, trace = await fallback("explique a gravidade", snap, options, infer, {"origin_area": "sala"})
            self.assertEqual(proposed, ())
            self.assertEqual(trace["llm"]["status"], "error")
            self.assertEqual(trace["llm"]["reason"], "llm_conversation_empty")

    async def test_v2_conversation_with_contradictory_operations_is_rejected(self):
        snap = _sample_snapshot()
        options = {"decision_enabled": False, "llm_enabled": True}

        async def infer(body):
            return {
                "contract_version": 2,
                "kind": "conversation",
                "response_text": "Tudo bem!",
                "operations": [{"action": "turn_on", "targets": ["light.sala"]}],
            }

        proposed, trace = await fallback("oi", snap, options, infer, {"origin_area": "sala"})
        self.assertEqual(proposed, ())
        self.assertEqual(trace["llm"]["status"], "error")
        self.assertEqual(trace["llm"]["reason"], "llm_conversation_contradiction")

    async def test_conversation_render_uses_query_success_without_error(self):
        res = SimpleNamespace(
            code="query_success",
            operation_count=0,
            response_text="Boa tarde! Como posso ajudar?",
            reason="llm_conversation",
            states=(),
        )
        speech, error_code, query = _render(res)
        self.assertEqual(speech, "Boa tarde! Como posso ajudar?")
        self.assertIsNone(error_code)
        self.assertTrue(query)


class TestAiLabConversationPipeline(unittest.IsolatedAsyncioTestCase):
    async def test_pipeline_conversation_preserves_response_and_clears_clarification(self):
        async def nlu(data):
            return {"intent": "unknown", "confidence": 0.1}

        async def decision(data):
            return {"decision_status": "abstained"}

        async def llm(data):
            return {
                "contract_version": 2,
                "kind": "conversation",
                "response_text": "A água ferve a 100 graus Celsius ao nível do mar.",
            }

        result = await pipeline("a que temperatura a água ferve?", nlu, decision, llm, mode="llm", general=True)
        self.assertEqual(result["source"], "llm")
        self.assertEqual(result["kind"], "conversation")
        self.assertEqual(result["final_kind"], "conversation_response")
        self.assertEqual(result["response_text"], "A água ferve a 100 graus Celsius ao nível do mar.")
        self.assertIsNone(result["clarification_question"])
        self.assertFalse(result["requires_review"])

    async def test_pipeline_conversation_empty_marks_error_and_no_device_clarification(self):
        async def nlu(data):
            return {"intent": "unknown", "confidence": 0.1}

        async def decision(data):
            return {"decision_status": "abstained"}

        async def llm(data):
            return {"contract_version": 2, "kind": "conversation", "response_text": None}

        result = await pipeline("me conte uma história", nlu, decision, llm, mode="llm", general=True)
        self.assertEqual(result["kind"], "conversation")
        self.assertEqual(result["final_kind"], "error")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "llm_conversation_empty")
        self.assertEqual(result["stages"]["llm"]["status"], "error")
        self.assertEqual(result["stages"]["llm"]["reason"], "llm_conversation_empty")
        self.assertIsNone(result["clarification_question"])
        self.assertIsNone(result["response_text"])

    def test_ai_lab_panel_js_speak_never_falls_back_to_clarification_on_conversation(self):
        panel_js = (Path(__file__).resolve().parents[1] / "custom_components/local_nlu/ai_lab_panel.js").read_text(encoding="utf-8")
        self.assertIn("llm_conversation_empty", panel_js)
        self.assertIn("this.last?.kind === 'conversation'", panel_js)
        self.assertIn("Erro: o LLM retornou resposta conversacional vazia (llm_conversation_empty).", panel_js)


if __name__ == "__main__":
    unittest.main()
