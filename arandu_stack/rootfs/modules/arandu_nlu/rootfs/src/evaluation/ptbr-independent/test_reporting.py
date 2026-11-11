"""Tests for report aggregation and Markdown rendering."""
from __future__ import annotations

import unittest

from reporting import render_markdown, summary


class ReportingTests(unittest.TestCase):
    def test_known_gaps_are_separate_from_scored_summary(self) -> None:
        cases = [
            {"id": "pass", "bucket": "expected-pass-now", "classification": "exact_pass", "detail": "ok"},
            {"id": "gap", "bucket": "known-gap-future", "classification": "semantic_mismatch", "detail": "gap"},
        ]
        report = {
            "freeze_id": "ptbr-independent-v1",
            "endpoint": "http://127.0.0.1:1",
            "summary": summary(cases),
            "cases": cases,
        }
        rendered = render_markdown(report)
        self.assertEqual(report["summary"]["scored_cases"], 1)
        self.assertIn("## Divergências pontuadas", rendered)
        self.assertIn("## Known gaps não pontuados", rendered)
        self.assertNotIn("`gap` | `known-gap-future`", rendered)
        self.assertIn("`gap` | `semantic_mismatch`", rendered)


if __name__ == "__main__":
    unittest.main()
