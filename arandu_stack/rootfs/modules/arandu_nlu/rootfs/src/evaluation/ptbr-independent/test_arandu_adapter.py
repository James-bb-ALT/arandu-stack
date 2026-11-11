"""Tests for local-only HTTP adapter boundaries."""
from __future__ import annotations

import unittest

from unittest.mock import patch

from arandu_adapter import AdapterError, LocalServer, decode_response_json, local_endpoint, _request


class AdapterEndpointTests(unittest.TestCase):
    def test_accepts_loopback_ipv4(self) -> None:
        self.assertEqual(local_endpoint("http://127.0.0.1:8765"), ("127.0.0.1", 8765))

    def test_accepts_loopback_ipv6(self) -> None:
        self.assertEqual(local_endpoint("http://[::1]:8765"), ("::1", 8765))

    def test_rejects_non_origin_path(self) -> None:
        with self.assertRaisesRegex(AdapterError, "origin-only"):
            local_endpoint("http://127.0.0.1:8765/v1/interpret")

    def test_rejects_embedded_credentials(self) -> None:
        with self.assertRaisesRegex(AdapterError, "no credentials"):
            local_endpoint("http://user:secret@127.0.0.1:8765")

    def test_rejects_remote_address(self) -> None:
        with self.assertRaisesRegex(AdapterError, "loopback"):
            local_endpoint("http://192.0.2.1:8765")


class AdapterMemoryTests(unittest.TestCase):
    def test_reads_rss_and_hwm_from_server_pid(self) -> None:
        process = type("Process", (), {"pid": 4242})()
        server = LocalServer(process=process, endpoint="http://127.0.0.1:8765")
        status = "Name:\tlocal-nlu\nVmRSS:\t748 kB\nVmHWM:\t1024 kB\n"
        with patch("arandu_adapter.Path.read_text", return_value=status) as read_text:
            self.assertEqual(server.memory_bytes(), {"rss_bytes": 748 * 1024, "hwm_bytes": 1024 * 1024})
        read_text.assert_called_once_with(encoding="ascii")

    def test_returns_empty_memory_when_procfs_missing(self) -> None:
        process = type("Process", (), {"pid": 4242})()
        server = LocalServer(process=process, endpoint="http://127.0.0.1:8765")
        with patch("arandu_adapter.Path.read_text", side_effect=OSError("missing")):
            self.assertEqual(server.memory_bytes(), {"rss_bytes": None, "hwm_bytes": None})


class AdapterWireTests(unittest.TestCase):
    def test_rejects_duplicate_json_keys(self) -> None:
        with self.assertRaisesRegex(AdapterError, "strict JSON"):
            decode_response_json(b'{"status":"ok","status":"bad"}')

    def test_rejects_non_finite_json_constants(self) -> None:
        for payload in (b'{"value":NaN}', b'{"value":Infinity}', b'{"value":-Infinity}'):
            with self.subTest(payload=payload), self.assertRaisesRegex(AdapterError, "strict JSON"):
                decode_response_json(payload)

    def test_rejects_invalid_utf8_and_json(self) -> None:
        for payload in (b'\\xff', b'{invalid'):
            with self.subTest(payload=payload), self.assertRaisesRegex(AdapterError, "strict JSON"):
                decode_response_json(payload)

    def test_rejects_oversized_response(self) -> None:
        response = type("Response", (), {
            "status": 200,
            "read": lambda self, limit: b"x" * (limit),
            "getheader": lambda self, name, default="": "application/json",
        })()
        connection = type("Connection", (), {
            "request": lambda self, *args, **kwargs: None,
            "getresponse": lambda self: response,
            "close": lambda self: None,
        })()
        with patch("arandu_adapter.http.client.HTTPConnection", return_value=connection):
            with self.assertRaisesRegex(AdapterError, "exceeds HTTP v1 limit"):
                _request("http://127.0.0.1:8765", "GET", "/health")

    def test_rejects_non_success_http_status(self) -> None:
        response = type("Response", (), {
            "status": 500,
            "read": lambda self, limit: b'{"error":"failed"}',
            "getheader": lambda self, name, default="": "application/json",
        })()
        connection = type("Connection", (), {
            "request": lambda self, *args, **kwargs: None,
            "getresponse": lambda self: response,
            "close": lambda self: None,
        })()
        with patch("arandu_adapter.http.client.HTTPConnection", return_value=connection):
            with self.assertRaisesRegex(AdapterError, "unexpected HTTP status: 500"):
                _request("http://127.0.0.1:8765", "GET", "/health")


if __name__ == "__main__":
    unittest.main()
