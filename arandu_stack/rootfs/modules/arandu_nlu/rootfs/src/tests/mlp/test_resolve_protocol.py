"""FIXTURE_TECNICA v2 resolution protocol and client tests."""

from __future__ import annotations

import json
import unittest

from custom_components.local_nlu.client import ClientError, LocalNluClient
from custom_components.local_nlu.protocol import (
    ProtocolError,
    parse_v2_plan,
    parse_v2_response,
    parse_v3_plan,
)


class _Content:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)

    async def read(self, _: int) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""


class _Response:
    def __init__(self, value: object, *, status: int = 200) -> None:
        body = json.dumps(value, separators=(",", ":")).encode()
        self.status = status
        self.headers = {"Content-Length": str(len(body))}
        self.content = _Content([body])

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


class ResolveClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolve_posts_to_versioned_path_with_bounds(self) -> None:
        session = _Session(
            _Response(
                {
                    "candidates": ["a", "b"],
                    "outcome": "ambiguous",
                }
            )
        )
        client = LocalNluClient(session, "http://127.0.0.1:11555")

        await client.async_resolve({"mention": "Abajur"})

        call = session.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertTrue(call["url"].endswith("/v2/resolve"))
        self.assertEqual(call["allow_redirects"], False)
        self.assertEqual(
            call["headers"]["Content-Type"], "application/json"
        )

        with self.assertRaises(ClientError):
            await client.async_resolve({"mention": "x" * 65_536})
        self.assertEqual(len(session.calls), 1)

    async def test_non_200_resolve_status_fails_closed(self) -> None:
        session = _Session(
            _Response({"status": "invalid_request", "version": 2}, status=400)
        )
        client = LocalNluClient(session, "http://127.0.0.1:11555")

        with self.assertRaises(ClientError):
            await client.async_resolve({"mention": "Abajur"})

    async def test_interpret_v2_posts_to_versioned_path(self) -> None:
        session = _Session(_Response({"status": "no_match", "version": 2}))
        client = LocalNluClient(session, "http://127.0.0.1:11555")

        await client.async_interpret_v2({"text": "Acenda"})

        call = session.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertTrue(call["url"].endswith("/v2/interpret"))


class ResolveProtocolTests(unittest.TestCase):
    def test_accepts_all_three_outcomes(self) -> None:
        resolved = parse_v2_response(
            {
                "evidence": "explicit_registry_alias",
                "outcome": "resolved",
                "registry_id": "reg_1",
            }
        )
        self.assertEqual(
            (resolved.status, resolved.registry_id, resolved.evidence),
            ("resolved", "reg_1", "explicit_registry_alias"),
        )
        ambiguous = parse_v2_response(
            {"candidates": ["a", "b"], "outcome": "ambiguous"}
        )
        self.assertEqual(ambiguous.candidates, ("a", "b"))
        no_match = parse_v2_response({"outcome": "no_match"})
        self.assertEqual(no_match.status, "no_match")

    def test_rejects_v1_shapes_and_weak_candidates(self) -> None:
        invalid = (
            {"status": "no_match", "version": 1},
            {"outcome": "resolved", "registry_id": "r"},
            {
                "evidence": "display_name_with_constraint",
                "outcome": "resolved",
                "registry_id": "r",
                "extra": 1,
            },
            {"outcome": "resolved", "registry_id": "r", "evidence": "fuzzy"},
            {"outcome": "ambiguous", "candidates": ["only"]},
            {"outcome": "ambiguous", "candidates": ["b", "a"]},
            {"outcome": "ambiguous", "candidates": ["a", "a"]},
            {"outcome": "no_match", "candidates": []},
            {"outcome": "plan"},
        )
        for response in invalid:
            with self.subTest(response=response), self.assertRaises(ProtocolError):
                parse_v2_response(response)


class ResolvePlanTests(unittest.TestCase):
    def test_accepts_versioned_plan(self) -> None:
        outcome = parse_v2_plan(
            {
                "operations": [
                    {"action": "turn_on", "targets": ["reg_main"]},
                    {
                        "action": "set_fan_percentage",
                        "percentage": 50,
                        "targets": ["reg_fan"],
                    },
                ],
                "status": "plan",
                "version": 2,
            }
        )

        self.assertEqual(outcome.status, "plan")
        self.assertEqual(
            [operation.action for operation in outcome.operations],
            ["turn_on", "set_fan_percentage"],
        )

    def test_rejects_v1_shapes_and_weak_plans(self) -> None:
        invalid = (
            {
                "operations": [{"action": "turn_on", "targets": ["r"]}],
                "status": "plan",
                "version": 1,
            },
            {"status": "ambiguous", "version": 1},
            {
                "operations": [{"action": "turn_on", "targets": ["same"]}],
                "status": "plan",
                "version": 2,
                "extra": True,
            },
            {
                "operations": [
                    {"action": "turn_off", "targets": ["same"]},
                    {"action": "turn_on", "targets": ["same"]},
                ],
                "status": "plan",
                "version": 2,
            },
            {
                "operations": [
                    {"action": "turn_on", "targets": ["z", "a"]}
                ],
                "status": "plan",
                "version": 2,
            },
            {"outcome": "no_match"},
        )
        for response in invalid:
            with self.subTest(response=response), self.assertRaises(ProtocolError):
                parse_v2_plan(response)

    def test_accepts_music_assistant_v3_plan(self) -> None:
        outcome = parse_v3_plan(
            {
                "music": {
                    "action": "play",
                    "media_query": "Queen",
                    "provider": "deezer",
                    "player": "media_player.sala_ma",
                    "queue_mode": "replace",
                },
                "status": "plan",
                "version": 3,
            }
        )

        self.assertEqual(outcome.status, "plan")
        self.assertEqual(outcome.music.provider, "deezer")
        self.assertEqual(outcome.music.player, "media_player.sala_ma")

    def test_rejects_direct_provider_or_weak_music_v3_shapes(self) -> None:
        invalid = (
            {
                "music": {
                    "action": "play",
                    "media_query": "Queen",
                    "player": "spotify",
                },
                "status": "plan",
                "version": 3,
            },
            {
                "music": {
                    "action": "play",
                    "provider": "spotify",
                    "player": "media_player.sala_ma",
                },
                "status": "plan",
                "version": 3,
            },
            {
                "music": {
                    "action": "set_volume",
                    "player": "media_player.sala_ma",
                },
                "status": "plan",
                "version": 3,
            },
        )
        for response in invalid:
            with self.subTest(response=response), self.assertRaises(ProtocolError):
                parse_v3_plan(response)


if __name__ == "__main__":
    unittest.main()
