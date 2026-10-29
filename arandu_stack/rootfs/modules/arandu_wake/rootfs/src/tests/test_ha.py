import json
import unittest
from unittest.mock import patch
from pathlib import Path

from arandu_wake.ha import assess_pipeline, connect, request


class FakeSocket:
    def __init__(self, messages):
        self.messages = iter(messages)
        self.sent = []
        self.closed = False

    def recv(self):
        return json.dumps(next(self.messages))

    def send(self, data):
        self.sent.append(json.loads(data))

    def close(self):
        self.closed = True


class HaTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((Path(__file__).resolve().parents[1] / "config/satellites.example.json").read_text())
        self.pipeline = {"id": "id-arandu", "name": "Arandu", "stt_engine": "stt.arandu", "tts_engine": "tts.arandu", "conversation_engine": "conversation.arandu", "wake_word_entity": "wake_word.openwakeword", "wake_word_id": "alexa"}

    def test_name_alone_does_not_prove_arandu(self):
        self.assertFalse(assess_pipeline(self.config, [self.pipeline])["ready"])

    def test_exact_engines_are_required(self):
        self.config["home_assistant"]["expected_engines"] = {key: self.pipeline[key] for key in ("stt_engine", "tts_engine", "conversation_engine")}
        self.assertTrue(assess_pipeline(self.config, [self.pipeline])["ready"])
        self.pipeline["stt_engine"] = "stt.unexpected"
        self.assertFalse(assess_pipeline(self.config, [self.pipeline])["ready"])

    def test_duplicate_names_fail_closed(self):
        self.assertFalse(assess_pipeline(self.config, [self.pipeline, dict(self.pipeline, id="other")])["ready"])

    def test_id_selects_exact_pipeline(self):
        self.config["home_assistant"]["pipeline_id"] = "id-arandu"
        result = assess_pipeline(self.config, [self.pipeline, dict(self.pipeline, id="other")])
        self.assertEqual(result["pipeline_id"], "id-arandu")

    def test_wrong_wake_word(self):
        self.pipeline["wake_word_id"] = "okay_nabu"
        self.assertIn("Pipeline sem wake word central Alexa", assess_pipeline(self.config, [self.pipeline])["issues"])

    def test_null_wake_word_is_reported_as_not_ready(self):
        self.pipeline["wake_word_id"] = None
        result = assess_pipeline(self.config, [self.pipeline])
        self.assertFalse(result["ready"])
        self.assertIn("Pipeline sem wake word central Alexa", result["issues"])

    def test_rejected_auth_closes_socket(self):
        ws = FakeSocket([{"type": "auth_required"}, {"type": "auth_invalid"}])
        with patch("websocket.create_connection", return_value=ws), self.assertRaises(ValueError):
            with connect("http://localhost:8123", "secret"):
                self.fail("Must not authenticate")
        self.assertTrue(ws.closed)

    def test_no_token_never_connects(self):
        with patch("websocket.create_connection") as create, self.assertRaises(ValueError):
            with connect("http://localhost:8123", ""):
                pass
        create.assert_not_called()

    def test_request_matches_id(self):
        ws = FakeSocket([{"id": 99}, {"id": 1, "success": True, "result": {"pipelines": []}}])
        self.assertEqual(request(ws, 1, "assist_pipeline/pipeline/list"), {"pipelines": []})
