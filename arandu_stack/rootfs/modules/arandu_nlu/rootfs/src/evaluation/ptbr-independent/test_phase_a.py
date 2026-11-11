"""Tests for Phase A corpus and independent oracle."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from corpus import load_fixtures
from phase_a import cases, compare, validate_cases


class PhaseATests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).parent
        self.catalog, _ = load_fixtures(self.root)

    def test_candidate_corpus_has_positive_negative_and_ambiguous_cases(self) -> None:
        items = cases()
        validate_cases(items, self.catalog)
        self.assertEqual(len(items), 6)
        self.assertEqual({item["bucket"] for item in items}, {"expected-pass-now", "expected-reject-now"})
        self.assertTrue(any(item["expected"]["status"] == "ambiguous" for item in items))

    def test_generated_corpus_matches_deterministic_oracle(self) -> None:
        generated = self.root / "data" / "dataset-phase-a.jsonl"
        expected = [json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for item in cases()]
        self.assertTrue(generated.is_file(), generated)
        self.assertEqual(generated.read_text(encoding="utf-8").splitlines(), expected)

    def test_oracle_accepts_canonical_plan(self) -> None:
        item = cases()[0]
        self.assertEqual(compare(item, item["expected"], self.catalog).classification, "exact_pass")

    def test_oracle_rejects_unsafe_plan(self) -> None:
        item = cases()[-1]
        unsafe = {"status": "plan", "version": 1, "operations": [{"action": "set_fan_percentage", "targets": ["reg_sensor_temperatura_sala"], "percentage": 40}]}
        self.assertEqual(compare(item, unsafe, self.catalog).classification, "protocol_error")


if __name__ == "__main__":
    unittest.main()
