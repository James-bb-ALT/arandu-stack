"""Tests for Phase A diagnostic runner."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import phase_a_run


class PhaseARunnerTests(unittest.TestCase):
    def test_adapter_error_is_recorded_as_protocol_error(self) -> None:
        with patch("phase_a_run.health"), patch("phase_a_run.interpret", side_effect=phase_a_run.AdapterError("wire")):
            results = phase_a_run.run_cases("http://127.0.0.1:1", {"version": 1}, object())
        self.assertTrue(all(item["classification"] == "protocol_error" for item in results))

    def test_report_is_not_written_when_manifest_verification_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "phase-a.json"
            arguments = type("Arguments", (), {"binary": None, "endpoint": "http://127.0.0.1:1", "output": output})()
            with patch("phase_a_run.parse_arguments", return_value=arguments), patch("phase_a_run.verify_manifest", side_effect=ValueError("drift")):
                with self.assertRaises(ValueError):
                    phase_a_run.main()
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
