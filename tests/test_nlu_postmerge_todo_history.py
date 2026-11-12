"""Post-merge v11.0.1 regression tests for Phase 5 todo fidelity & partial history (P1)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from custom_components.local_nlu import advanced_queries as history
from custom_components.local_nlu.queries import QueryError
from custom_components.local_nlu.todo_actions import bind


class _MockTodoServices:
    def __init__(self, items: list[dict]) -> None:
        self.items = items
        self.calls: list[dict] = []

    async def async_call(self, domain, service, data, blocking=False, context=None, target=None, return_response=False):
        self.calls.append({"domain": domain, "service": service, "data": dict(data), "target": target})
        if domain == "todo" and service == "get_items":
            return {target["entity_id"][0]: {"items": list(self.items)}}
        return {}


class TestPostmergeTodoAndHistory(unittest.IsolatedAsyncioTestCase):
    async def test_todo_bind_preserves_accents_and_casing_and_rejects_homonyms(self) -> None:
        services = _MockTodoServices([
            {"uid": "u_cafe", "summary": "café", "status": "needs_action"},
            {"uid": "u_pao", "summary": "pão", "status": "needs_action"},
        ])
        hass = SimpleNamespace(services=services)

        async def _auth():
            return None

        payload, old_summary = await bind(
            hass,
            {"entity_id": "todo.compras", "data": {"item": "cafe", "rename": "Café Especial"}},
            "todo_update",
            None,
            _auth,
        )
        self.assertEqual(payload["item"], "u_cafe")
        self.assertEqual(payload["rename"], "Café Especial")
        self.assertEqual(old_summary, "café")

        # Homonyms must be rejected
        services_dup = _MockTodoServices([
            {"uid": "u1", "summary": "Café", "status": "needs_action"},
            {"uid": "u2", "summary": "café", "status": "needs_action"},
        ])
        hass_dup = SimpleNamespace(services=services_dup)
        with self.assertRaises(QueryError) as ctx:
            await bind(hass_dup, {"entity_id": "todo.compras", "data": {"item": "café", "rename": "Café Especial"}}, "todo_update", None, _auth)
        self.assertEqual(str(ctx.exception), "todo_item_not_unique")

        # Operational verb inside item content is data only
        payload_op, _ = await bind(
            hass,
            {"entity_id": "todo.compras", "data": {"item": "desliga a luz"}},
            "todo_add",
            None,
            _auth,
        )
        self.assertEqual(payload_op["item"], "desliga a luz")
        self.assertEqual([c for c in services.calls if c["domain"] in ("light", "lock")], [])

    def test_energy_partial_today_at_1037_reports_coverage_without_extrapolating(self) -> None:
        tz_sp = timezone(timedelta(hours=-3))
        start_local = datetime(2026, 11, 12, 0, 0, tzinfo=tz_sp)
        end_local = datetime(2026, 11, 12, 10, 37, tzinfo=tz_sp)
        start_utc = start_local.astimezone(timezone.utc)
        end_utc = end_local.astimezone(timezone.utc)

        rows = [{"entity_id": "sensor.medidor", "name": "Medidor", "attributes": {"unit_of_measurement": "kWh"}}]
        # Hourly sums from 00:00 (start-1h) through 10:00 (last completed bin start=09:00 local -> end=10:00 local)
        points = [
            {"start": (start_utc + timedelta(hours=i - 1)).timestamp(), "sum": float(100 + i * 1.5)}
            for i in range(11)
        ]
        answer = history.energy_result({"sensor.medidor": points}, rows, start_utc, end_utc, local_tz=tz_sp)
        self.assertIn("15 kWh", answer)
        self.assertIn("período parcial", answer)
        self.assertIn("dados de 00:00 até 10:00", answer)

        # Missing an intermediate hour must still fail
        with self.assertRaises(QueryError):
            history.energy_result({"sensor.medidor": points[:4] + points[5:]}, rows, start_utc, end_utc, local_tz=tz_sp)

        # Meter reset (non-monotonic sum) must fail
        points_reset = [dict(p) for p in points]
        points_reset[5]["sum"] = 0.0
        with self.assertRaises(QueryError):
            history.energy_result({"sensor.medidor": points_reset}, rows, start_utc, end_utc, local_tz=tz_sp)
