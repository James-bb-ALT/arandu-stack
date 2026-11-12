"""Tests for frozen HTTP benchmark rendering."""
from __future__ import annotations

import unittest

from benchmark import render_markdown


class BenchmarkReportingTests(unittest.TestCase):
    def test_renders_steady_state_and_cold_start_summary(self) -> None:
        statistics = {
            "count": 7,
            "min_ns": 1,
            "p50_ns": 2,
            "p95_ns": 3,
            "p99_ns": 4,
            "max_ns": 5,
            "mean_ns": 3.0,
        }
        report = {
            "freeze_id": "ptbr-independent-v1",
            "http_steady_state": {
                "requests_per_round": 144,
                "warmup_rounds_discarded": 3,
                "measured_rounds": 7,
                "request_latency": statistics,
                "round_duration": statistics,
                "throughput_requests_per_second": 12.5,
            },
            "cold_start": {"samples": [{"classification": "exact_pass"}]},
            "summary": {
                "failures": 0,
                "cold_failures": 0,
                "rss": "not collected",
            },
        }

        rendered = render_markdown(report)

        self.assertIn("# Benchmark HTTP PT-BR independente", rendered)
        self.assertIn("- Casos por rodada: 144", rendered)
        self.assertIn("- Amostras cold start: 1", rendered)
        self.assertIn("- Throughput: 12.5 requests/s", rendered)


if __name__ == "__main__":
    unittest.main()
