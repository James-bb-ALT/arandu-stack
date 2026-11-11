"""Tests for frozen evaluation runner behavior."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import run
from arandu_adapter import AdapterError


class RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = {"areas": [], "entities": []}
        self.cases = [
            {"id": "pass", "bucket": "expected-pass-now", "raw": {"text": "x", "dimensions": {"domain": "light"}}, "expected": {"status": "plan", "version": 1, "operations": []}},
            {"id": "gap", "bucket": "known-gap-future", "raw": {"text": "y", "dimensions": {"domain": "light"}}, "expected": {"status": "plan", "version": 1, "operations": []}},
        ]

    def test_adapter_error_becomes_protocol_error(self) -> None:
        with patch("run.health"), patch("run.interpret", side_effect=AdapterError("wire failed")):
            results = run.run_cases("http://127.0.0.1:1", self.catalog, self.cases, self.catalog)
        self.assertTrue(all(item["classification"] == "protocol_error" for item in results))

    def test_report_separates_known_gaps(self) -> None:
        results = [
            {"id": "pass", "bucket": "expected-pass-now", "dimensions": {"domain": "light"}, "classification": "exact_pass", "detail": ""},
            {"id": "gap", "bucket": "known-gap-future", "dimensions": {"domain": "light"}, "classification": "unsafe_acceptance", "detail": "gap"},
        ]
        report = run.build_report("http://127.0.0.1:1", self.cases, results, None)
        self.assertEqual(report["summary"]["scored_cases"], 1)
        self.assertEqual(report["summary"]["known_gap_cases"], 1)
        with tempfile.TemporaryDirectory() as directory:
            json_path = Path(directory) / "run.json"
            markdown_path = Path(directory) / "run.md"
            from reporting import write_report
            write_report(report, json_path, markdown_path)
            self.assertEqual(json.loads(json_path.read_text(encoding="utf-8")), report)
            self.assertIn("Known gaps não pontuados", markdown_path.read_text(encoding="utf-8"))

    def test_main_verifies_freeze_before_starting_server(self) -> None:
        arguments = type("Arguments", (), {"binary": Path("server"), "endpoint": None, "output_dir": Path("target")})()
        order: list[str] = []
        manifest = type("Manifest", (), {"exists": lambda self: True, "read_text": lambda self, **kwargs: "{}"})()
        with patch("run.parse_arguments", return_value=arguments), patch("run.MANIFEST", manifest), patch("run.verify_manifest", side_effect=lambda _: order.append("freeze")), patch("run.load_fixtures", return_value=(self.catalog, self.cases)), patch("run.start_local", side_effect=lambda _: order.append("start")):
            with self.assertRaises(Exception):
                run.main()
        self.assertEqual(order[:2], ["freeze", "start"])


if __name__ == "__main__":
    unittest.main()
