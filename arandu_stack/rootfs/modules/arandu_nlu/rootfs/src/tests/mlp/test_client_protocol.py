"""FIXTURE_TECNICA client and response-contract tests."""

from __future__ import annotations

import json
import unittest

from custom_components.local_nlu.client import (
    ClientError,
    LocalNluClient,
    normalize_endpoint,
)
from custom_components.local_nlu.protocol import ProtocolError, parse_response


class _Content:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)

    async def read(self, _: int) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""


class _Response:
    def __init__(
        self,
        value: object,
        *,
        status: int = 200,
        declared: int | None = None,
        chunks: list[bytes] | None = None,
    ) -> None:
        body = json.dumps(value, separators=(",", ":")).encode()
        self.status = status
        self.headers = {
            "Content-Length": str(len(body) if declared is None else declared)
        }
        self.content = _Content(chunks if chunks is not None else [body])

    async def __aenter__(self) -> _Response:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


class _Session:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        data: bytes | None,
        headers: dict[str, str],
        allow_redirects: bool,
    ) -> _Response:
        self.calls.append(
            {
                "allow_redirects": allow_redirects,
                "data": data,
                "headers": headers,
                "method": method,
                "url": url,
            }
        )
        return self.response


class _Resolver:
    def __init__(self, *answers: tuple[str, ...]) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, int]] = []

    async def __call__(self, host: str, port: int) -> tuple[str, ...]:
        self.calls.append((host, port))
        return self.answers.pop(0)


class EndpointTests(unittest.TestCase):
    def test_accepts_only_canonical_local_origins(self) -> None:
        self.assertEqual(
            normalize_endpoint("http://LOCAL-PTBR-NLU:11555/"),
            "http://local-ptbr-nlu:11555",
        )
        self.assertEqual(
            normalize_endpoint("http://192.168.1.20:11555"),
            "http://192.168.1.20:11555",
        )
        self.assertEqual(
            normalize_endpoint("http://nlu.home.arpa:11555"),
            "http://nlu.home.arpa:11555",
        )

    def test_accepts_store_assigned_hostname_form(self) -> None:
        # 18b0d50a is SHA-1("https://github.com/jaimevictor/Arandu-NLU",
        # lower)[:8]: the Supervisor hostname for this repository's store.
        # A repo rename/transfer changes it; this test fails loudly then.
        self.assertEqual(
            normalize_endpoint("http://18B0D50A-PTBR-NLU:11555/"),
            "http://18b0d50a-ptbr-nlu:11555",
        )

    def test_rejects_public_credentials_paths_and_tls(self) -> None:
        rejected = (
            "https://local-ptbr-nlu:11555",
            "http://example.com:11555",
            "http://8.8.8.8:11555",
            "http://user:secret@local-ptbr-nlu:11555",
            "http://local-ptbr-nlu:11555/path",
            "http://local-ptbr-nlu",
            "http://134744072:11555",
            "http://169.254.169.254:11555",
            "http://bad_host:11555",
            "http://-bad:11555",
            "http://" + "a" * 64 + ":11555",
            "http://ptbr-nlu:11555",
            "http://my-addon123:11555",
            "http://ab-ptbr-nlu:11555",
            "http://xyz-ptbr-nlu:11555",
            "http://3283f0a1-ptbr-nlu.evil.com:11555",
        )
        for endpoint in rejected:
            with self.subTest(endpoint=endpoint), self.assertRaises(ClientError):
                normalize_endpoint(endpoint)


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_health_uses_only_the_closed_local_endpoint(self) -> None:
        session = _Session(_Response({"status": "ok", "version": 1}))
        resolver = _Resolver(("172.30.32.2",))
        client = LocalNluClient(
            session,
            "http://local-ptbr-nlu:11555",
            resolver=resolver,
        )

        await client.async_health()

        self.assertEqual(resolver.calls, [("local-ptbr-nlu", 11555)])
        self.assertEqual(
            session.calls,
            [
                {
                    "allow_redirects": False,
                    "data": None,
                    "headers": {"Host": "local-ptbr-nlu:11555"},
                    "method": "GET",
                    "url": "http://172.30.32.2:11555/health",
                }
            ],
        )

    async def test_oversized_response_fails_closed(self) -> None:
        session = _Session(
            _Response(
                {},
                declared=70_000,
                chunks=[b"{}"],
            )
        )
        client = LocalNluClient(session, "http://127.0.0.1:11555")

        with self.assertRaises(ClientError):
            await client.async_health()

    async def test_public_or_rebound_resolution_fails_before_request(self) -> None:
        session = _Session(_Response({"status": "ok", "version": 1}))
        resolver = _Resolver(
            ("172.30.32.2",),
            ("172.30.32.2", "8.8.8.8"),
        )
        client = LocalNluClient(
            session,
            "http://local-ptbr-nlu:11555",
            resolver=resolver,
        )

        await client.async_health()
        with self.assertRaises(ClientError):
            await client.async_health()

        self.assertEqual(len(session.calls), 1)

    async def test_interpretation_body_is_exact_and_bounded(self) -> None:
        session = _Session(_Response({"status": "no_match", "version": 1}))
        client = LocalNluClient(session, "http://127.0.0.1:11555")

        await client.async_interpret({"version": 1, "text": "Lâmpada"})

        self.assertEqual(
            session.calls[0]["data"],
            b'{"text":"L\xc3\xa2mpada","version":1}',
        )
        self.assertEqual(
            session.calls[0]["headers"],
            {
                "Content-Type": "application/json",
                "Host": "127.0.0.1:11555",
            },
        )

        with self.assertRaises(ClientError):
            await client.async_interpret({"text": "x" * 65_536})
        self.assertEqual(len(session.calls), 1)


class ProtocolTests(unittest.TestCase):
    def test_accepts_ordered_mixed_effect_plan(self) -> None:
        outcome = parse_response(
            {
                "operations": [
                    {
                        "action": "turn_off",
                        "targets": ["reg_light_sala"],
                    },
                    {
                        "action": "turn_on",
                        "targets": ["reg_light_quarto"],
                    },
                    {
                        "action": "set_fan_percentage",
                        "percentage": 50,
                        "targets": ["reg_fan_quarto"],
                    },
                ],
                "status": "plan",
                "version": 1,
            }
        )

        self.assertEqual(
            [operation.action for operation in outcome.operations],
            ["turn_off", "turn_on", "set_fan_percentage"],
        )

    def test_rejects_contradiction_unsorted_targets_and_query_chain(self) -> None:
        invalid = (
            {
                "operations": [
                    {"action": "turn_off", "targets": ["same"]},
                    {"action": "turn_on", "targets": ["same"]},
                ],
                "status": "plan",
                "version": 1,
            },
            {
                "operations": [
                    {"action": "turn_on", "targets": ["z", "a"]}
                ],
                "status": "plan",
                "version": 1,
            },
            {
                "operations": [
                    {"action": "get_state", "targets": ["sensor"]},
                    {"action": "turn_on", "targets": ["light"]},
                ],
                "status": "plan",
                "version": 1,
            },
        )
        for response in invalid:
            with self.subTest(response=response), self.assertRaises(ProtocolError):
                parse_response(response)

    def test_rejects_unknown_fields_and_boolean_percentage(self) -> None:
        invalid = (
            {
                "extra": True,
                "status": "no_match",
                "version": 1,
            },
            {
                "operations": [
                    {
                        "action": "set_fan_percentage",
                        "percentage": True,
                        "targets": ["fan"],
                    }
                ],
                "status": "plan",
                "version": 1,
            },
        )
        for response in invalid:
            with self.subTest(response=response), self.assertRaises(ProtocolError):
                parse_response(response)


if __name__ == "__main__":
    unittest.main()
