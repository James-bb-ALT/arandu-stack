import json
import os
import sys
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

import wyoming_discovery


class DummyResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class TestWyomingDiscovery(TestCase):
    def test_uri_uses_supervisor_hostname(self):
        def fake_urlopen(req, timeout):
            self.assertEqual(req.full_url, "http://supervisor/addons/self/info")
            return DummyResponse({"result": "ok", "data": {"hostname": "local_arandu-stt"}})

        with patch.object(wyoming_discovery, "urlopen", fake_urlopen):
            self.assertEqual(
                wyoming_discovery.discovery_uri(token="token"),
                "tcp://local-arandu-stt:10350",
            )

    def test_publish_posts_expected_payload(self):
        calls = []

        def fake_urlopen(req, timeout):
            calls.append((req.full_url, req.get_method(), req.data))
            if req.full_url.endswith("/addons/self/info"):
                return DummyResponse({"result": "ok", "data": {"hostname": "abc123_arandu-stt"}})
            return DummyResponse({"result": "ok", "data": {"uuid": "u1"}})

        with patch.dict(os.environ, {"SUPERVISOR_TOKEN": "token"}), patch.object(
            wyoming_discovery, "urlopen", fake_urlopen
        ):
            result = wyoming_discovery.publish_wyoming_discovery()

        self.assertEqual(result.uuid, "u1")
        self.assertEqual(result.uri, "tcp://abc123-arandu-stt:10350")
        self.assertEqual(calls[1][0], "http://supervisor/discovery")
        self.assertEqual(calls[1][1], "POST")
        self.assertEqual(
            json.loads(calls[1][2].decode()),
            {"service": "wyoming", "config": {"uri": "tcp://abc123-arandu-stt:10350"}},
        )

