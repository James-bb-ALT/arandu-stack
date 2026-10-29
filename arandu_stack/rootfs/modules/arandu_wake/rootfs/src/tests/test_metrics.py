import json
import unittest

from arandu_wake.metrics import summarize


class MetricsTests(unittest.TestCase):
    def test_latency_and_content_redaction(self):
        events = [{"type": kind, "timestamp": stamp, "data": {"text": "SECRET_TRANSCRIPT", "token": "SECRET_TOKEN"}}
                  for kind, stamp in (("run-start", 0), ("wake_word-start", 0.1), ("wake_word-end", 1), ("stt-start", 1.1), ("stt-end", 2), ("tts-start", 2.2), ("tts-end", 3), ("run-end", 3.2))]
        result = summarize(events)
        self.assertEqual(result["wake_stage_ms"], 900)
        self.assertEqual(result["capture_start_after_wake_ms"], 100)
        self.assertEqual(result["pipeline_ms"], 3200)
        self.assertEqual(result["playback_completed"], "unknown")
        self.assertNotIn("SECRET", json.dumps(result))

    def test_missing_events_are_unknown(self):
        self.assertIsNone(summarize([])["pipeline_ms"])

    def test_out_of_order(self):
        with self.assertRaises(ValueError):
            summarize([{"type": "run-start", "timestamp": 2}, {"type": "run-end", "timestamp": 1}])

    def test_multiple_sessions_rejected(self):
        with self.assertRaises(ValueError):
            summarize([{"type": "run-start", "timestamp": 0}, {"type": "run-start", "timestamp": 1}])

    def test_iso_timestamps(self):
        self.assertEqual(summarize([{"type": "run-start", "timestamp": "2026-10-14T12:00:00Z"}, {"type": "run-end", "timestamp": "2026-10-14T12:00:01Z"}])["pipeline_ms"], 1000)

    def test_nonfinite_timestamps_rejected(self):
        for value in (float("nan"), float("inf"), True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                summarize([{"type": "run-start", "timestamp": value}])
