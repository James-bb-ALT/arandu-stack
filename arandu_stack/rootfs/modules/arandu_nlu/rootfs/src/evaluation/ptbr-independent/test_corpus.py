"""Static checks for the frozen-input PT-BR evaluation corpus."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import unittest

from corpus import load_fixtures


class CorpusTests(unittest.TestCase):
    def test_v1_fixture_counts_and_provenance(self) -> None:
        catalog, cases = load_fixtures(Path(__file__).resolve().parent)
        self.assertTrue(catalog.all_targets)
        self.assertEqual(len(cases), 144)
        self.assertEqual(
            Counter(case["bucket"] for case in cases),
            {
                "expected-pass-now": 72,
                "expected-reject-now": 48,
                "known-gap-future": 24,
            },
        )
        for case in cases:
            self.assertEqual(case["raw"]["provenance"]["kind"], "PROJECT_AUTHORED_SYNTHETIC")
            self.assertEqual(case["raw"]["provenance"]["license"], "Apache-2.0")
            self.assertIs(case["raw"]["provenance"]["copied_text"], False)

    def test_known_gaps_are_not_current_score_cases(self) -> None:
        _, cases = load_fixtures(Path(__file__).resolve().parent)
        score_cases = [case for case in cases if case["bucket"] != "known-gap-future"]
        self.assertEqual(len(score_cases), 120)
        self.assertEqual(sum(case["bucket"] == "expected-pass-now" for case in score_cases), 72)
        self.assertEqual(sum(case["bucket"] == "expected-reject-now" for case in score_cases), 48)


if __name__ == "__main__":
    unittest.main()
