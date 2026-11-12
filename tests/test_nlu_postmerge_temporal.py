"""Post-merge v11.0.1 regression tests for temporal job lifecycle & disambiguation (P1)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from custom_components.local_nlu.scheduled_delivery import ScheduledDeliveryHandler
from custom_components.local_nlu.scheduled_jobs import (
    AmbiguousJobError,
    ScheduledJob,
    ScheduledJobManager,
)


def _make_hass(services_available: set[tuple[str, str]] | None = None):
    if services_available is None:
        services_available = {("persistent_notification", "create"), ("tts", "speak")}
    hass = SimpleNamespace()
    hass.calls = []
    hass.states = SimpleNamespace(get=lambda eid: SimpleNamespace(state="idle") if eid == "media_player.sala" else None)

    async def _async_call(domain, service, data, blocking=False, context=None, target=None):
        if (domain, service) not in services_available:
            raise RuntimeError(f"Service {domain}.{service} unavailable")
        hass.calls.append({"domain": domain, "service": service, "data": dict(data)})

    hass.services = SimpleNamespace(
        has_service=lambda d, s: (d, s) in services_available,
        async_call=_async_call,
    )
    hass.async_create_task = lambda coro: __import__("asyncio").create_task(coro)
    return hass


class _MemoryStore:
    def __init__(self, initial=None):
        self.data = initial

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.data = data


class TestPostmergeTemporal(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_requires_disambiguation_when_multiple_exist(self) -> None:
        hass = _make_hass()
        mgr = ScheduledJobManager(hass, delivery_handler=ScheduledDeliveryHandler(hass))
        mgr._store = _MemoryStore()

        j1 = await mgr.async_add_job(job_type="timer", target_action="timer", delay_seconds=300, name="pizza")
        j2 = await mgr.async_add_job(job_type="timer", target_action="timer", delay_seconds=600, name="massa")

        # Ambiguous cancel without name/all_of_type raises AmbiguousJobError, cancels 0
        with self.assertRaises(AmbiguousJobError) as ctx:
            await mgr.async_cancel_jobs(job_type="timer")
        self.assertEqual(len(ctx.exception.candidates), 2)
        self.assertEqual(len(mgr.list_jobs(job_type="timer")), 2)

        # Explicit job_id cancels only pizza
        cancelled = await mgr.async_cancel_jobs(job_type="timer", job_id=j1.id)
        self.assertEqual([j.id for j in cancelled], [j1.id])
        self.assertEqual([j.id for j in mgr.list_jobs(job_type="timer")], [j2.id])

        # Single remaining timer cancels cleanly without error
        cancelled2 = await mgr.async_cancel_jobs(job_type="timer")
        self.assertEqual([j.id for j in cancelled2], [j2.id])
        self.assertEqual(len(mgr.list_jobs(job_type="timer")), 0)

    async def test_all_of_type_explicit_cancels_all(self) -> None:
        hass = _make_hass()
        mgr = ScheduledJobManager(hass)
        mgr._store = _MemoryStore()
        await mgr.async_add_job(job_type="timer", target_action="timer", delay_seconds=100, name="a")
        await mgr.async_add_job(job_type="timer", target_action="timer", delay_seconds=200, name="b")

        cancelled = await mgr.async_cancel_jobs(job_type="timer", all_of_type=True)
        self.assertEqual(len(cancelled), 2)
        self.assertEqual(len(mgr.list_jobs(job_type="timer")), 0)

    async def test_change_snooze_dismiss_reject_ambiguity_and_distant_pending(self) -> None:
        hass = _make_hass()
        mgr = ScheduledJobManager(hass)
        mgr._store = _MemoryStore()
        await mgr.async_add_job(job_type="timer", target_action="timer", delay_seconds=100, name="a")
        await mgr.async_add_job(job_type="timer", target_action="timer", delay_seconds=200, name="b")

        with self.assertRaises(AmbiguousJobError):
            await mgr.async_change_timer(delta_seconds=60)

        # Distant pending alarm (> 5s away) cannot be snoozed or dismissed
        await mgr.async_add_job(job_type="alarm", target_action="clock_alarm", delay_seconds=3600, name="manha")
        self.assertIsNone(await mgr.async_snooze_job(job_type="alarm"))
        self.assertIsNone(await mgr.async_dismiss_job(job_type="alarm"))

    async def test_scheduled_action_cancel_filters_target_action_and_targets(self) -> None:
        hass = _make_hass()
        mgr = ScheduledJobManager(hass)
        mgr._store = _MemoryStore()
        j_tv = await mgr.async_add_job(
            job_type="action", target_action="turn_off", targets=["media_player.tv"], delay_seconds=600
        )
        j_light = await mgr.async_add_job(
            job_type="action", target_action="turn_off", targets=["light.sala"], delay_seconds=600
        )

        cancelled = await mgr.async_cancel_jobs(
            job_type="action", target_action="turn_off", targets=["media_player.tv"]
        )
        self.assertEqual([j.id for j in cancelled], [j_tv.id])
        self.assertEqual([j.id for j in mgr.list_jobs(job_type="action")], [j_light.id])

    async def test_delivered_timer_and_reminder_complete_and_free_quota(self) -> None:
        hass = _make_hass()
        delivery = ScheduledDeliveryHandler(hass, lambda: {"delivery_media_player": "media_player.sala", "delivery_tts_engine": "tts.piper"})
        mgr = ScheduledJobManager(hass, delivery_handler=delivery)
        mgr._store = _MemoryStore()

        j_timer = await mgr.async_add_job(job_type="timer", target_action="timer", delay_seconds=1, name="ovo")
        j_rem = await mgr.async_add_job(job_type="reminder", target_action="reminder", delay_seconds=1, title="remedio")
        j_alarm = await mgr.async_add_job(job_type="alarm", target_action="clock_alarm", delay_seconds=1, recurrence="daily", name="acordar")
        j_timer.due_timestamp = time.time() - 1
        j_rem.due_timestamp = time.time() - 1
        j_alarm.due_timestamp = time.time() - 1

        await mgr._async_handle_due_jobs()
        # Wait briefly for spawned dispatch tasks
        for task in list(mgr._tasks):
            await task

        self.assertEqual(j_timer.status, "completed")
        self.assertEqual(j_rem.status, "completed")
        self.assertEqual(j_alarm.status, "ringing")
        # Recurrence was created automatically without needing async_dismiss_job!
        pending_alarms = [j for j in mgr.list_jobs(job_type="alarm") if j.status == "pending"]
        self.assertEqual(len(pending_alarms), 1)
        # Dismissing the ringing alarm must NOT create a duplicate recurrence
        await mgr.async_dismiss_job(job_type="alarm", job_id=j_alarm.id)
        pending_alarms_after = [j for j in mgr.list_jobs(job_type="alarm") if j.status == "pending"]
        self.assertEqual(len(pending_alarms_after), 1)
        # Timer & reminder left active list
        self.assertEqual(len(mgr.list_jobs(job_type="timer")), 0)
        self.assertEqual(len(mgr.list_jobs(job_type="reminder")), 0)

    async def test_saturation_marks_failed_and_restart_migrates_legacy_ringing(self) -> None:
        hass = _make_hass()
        delivery = ScheduledDeliveryHandler(hass)
        mgr = ScheduledJobManager(hass, delivery_handler=delivery, max_active=2)
        mgr._store = _MemoryStore({
            "jobs": [
                {
                    "id": "oldsat",
                    "job_type": "timer",
                    "target_action": "timer",
                    "targets": [],
                    "due_timestamp": time.time() - 10,
                    "due_iso": "2026-11-12T10:00:00+00:00",
                    "status": "ringing",
                    "delivery_status": "saturated",
                },
                {
                    "id": "olddisp",
                    "job_type": "action",
                    "target_action": "turn_on",
                    "targets": ["light.sala"],
                    "due_timestamp": time.time() - 10,
                    "due_iso": "2026-11-12T10:00:00+00:00",
                    "status": "dispatching",
                    "delivery_status": "dispatching",
                },
            ]
        })
        await mgr.async_load()
        self.assertEqual(mgr._jobs["oldsat"].status, "failed")
        self.assertEqual(mgr._jobs["olddisp"].status, "interrupted")
        self.assertEqual(mgr.count_active(), 0)

    async def test_delivery_visual_only_and_total_error_honesty(self) -> None:
        # HA without tts.speak: visual notification accepted, tts unavailable
        hass_no_tts = _make_hass({("persistent_notification", "create")})
        delivery = ScheduledDeliveryHandler(
            hass_no_tts, lambda: {"delivery_media_player": "media_player.sala", "delivery_tts_engine": "tts.piper"}
        )
        job = ScheduledJob(id="j1", job_type="timer", target_action="timer", name="cha")
        ok = await delivery.async_deliver_notification(job)
        self.assertTrue(ok)
        self.assertEqual(job.notification_status, "notification_accepted")
        self.assertEqual(job.tts_status, "unavailable")

        # Both channels fail -> returns False
        hass_fail = _make_hass(set())
        delivery_fail = ScheduledDeliveryHandler(hass_fail)
        ok_fail = await delivery_fail.async_deliver_notification(job)
        self.assertFalse(ok_fail)
        self.assertEqual(job.notification_status, "delivery_error")
