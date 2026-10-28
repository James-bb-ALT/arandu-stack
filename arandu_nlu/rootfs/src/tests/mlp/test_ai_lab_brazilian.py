"""FIXTURE_TECNICA: typed-model contracts, no claims about model accuracy."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

from custom_components.local_nlu.ai_lab_core import MODELS

spec = importlib.util.spec_from_file_location("arandu_lab_adapters_test", Path(__file__).resolve().parents[2] / "tools/ai_lab/adapters.py")
adapters = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapters)


class BrazilianContracts(unittest.TestCase):
    def rows(self):
        return [{"entity_id": f"light.fixture_{i}", "intent": "turn_on", "text": f"FIXTURE_TECNICA {i}", "description": "FIXTURE_TECNICA"} for i in range(60)]

    def test_saracura_uses_upstream_tok_and_considers_every_device(self):
        calls = []
        def predict(text, questions, **kwargs):
            options = questions["action"]["criteria"]
            calls.append(options)
            return {"answers": {"action": {"probabilities": {key: 1 / len(options) for key in options}}}}
        encoder = adapters.Encoder(Path("fixture"))
        encoder.model = SimpleNamespace(tok=SimpleNamespace(encode=lambda _: [1]), predict=predict)
        result = encoder.typed_ranking("FIXTURE_TECNICA", self.rows(), "saracura")
        self.assertEqual(len(result), 60)
        self.assertEqual(len({r["entity_id"] for r in result}), 60)
        self.assertEqual(len(calls), 15)
        self.assertTrue(all("none" in call for call in calls))

    def test_jatoba_uses_raw_output_and_bounds_candidate_groups(self):
        calls = []
        def decide(text, question, options, calibrated):
            self.assertFalse(calibrated)
            self.assertLessEqual(len(options), 33)
            calls.append(options)
            return SimpleNamespace(probabilities={key: 1 / len(options) for key in options})
        encoder = adapters.Encoder(Path("fixture"))
        encoder.model = SimpleNamespace(decide=decide)
        self.assertEqual(len(encoder.typed_ranking("FIXTURE_TECNICA", self.rows(), "jatoba")), 60)
        self.assertEqual(len(calls), 2)

    def test_missing_none_or_unknown_options_are_rejected(self):
        encoder = adapters.Encoder(Path("fixture"))
        encoder.model = SimpleNamespace(decide=lambda *a, **kw: SimpleNamespace(probabilities={"invented": 1.0}))
        with self.assertRaises(ValueError):
            encoder.typed_ranking("FIXTURE_TECNICA", self.rows(), "jatoba")

    def test_verified_identifiers_and_noncommercial_disclosure(self):
        self.assertEqual(MODELS["tucano"]["source_repo"], "Polygl0t/Tucano2-qwen-0.5B-Instruct")
        self.assertTrue(MODELS["jatoba"]["noncommercial"])
        for key in ("saracura", "jatoba", "tucano"):
            self.assertNotIn("unavailable", MODELS[key])
