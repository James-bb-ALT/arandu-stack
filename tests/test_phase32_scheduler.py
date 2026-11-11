"""Unit tests for ScheduledJobManager lifecycle, persistence, and quotas."""
import asyncio
import time
import unittest
from unittest.mock import AsyncMock, Mock

from custom_components.local_nlu.scheduled_jobs import (
    MAX_ACTIVE_JOBS,
    ScheduledJob,
    ScheduledJobManager,
)


class FakeHass:
    def __init__(self):
        self.loop = asyncio.get_event_loop()
        self.services = Mock()
        self.states = Mock()
        self.tasks = []

    def async_create_task(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.append(task)
        return task


class TestScheduledJobManager(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass = FakeHass()
        self.delivery = Mock()
        self.delivery.async_deliver_notification = AsyncMock()
        self.execution = Mock()
        self.execution.async_execute_scheduled_action = AsyncMock(return_value=True)
        self.manager = ScheduledJobManager(self.hass, self.delivery, self.execution)

    async def asyncTearDown(self):
        self.manager.close()
        for t in self.hass.tasks:
            if not t.done():
                t.cancel()

    async def test_add_and_list_alarm(self):
        job = await self.manager.async_add_job(
            job_type="alarm",
            target_action="clock_alarm",
            time_str="07:00",
            name="trabalho",
            recurrence="weekdays",
        )
        self.assertIsNotNone(job.id)
        self.assertEqual(job.name, "trabalho")
        self.assertEqual(job.status, "pending")
        self.assertIn("07:00", job.due_iso)

        alarms = self.manager.list_jobs(job_type="alarm")
        self.assertEqual(len(alarms), 1)
        self.assertEqual(alarms[0].id, job.id)

    async def test_add_and_query_timer(self):
        job = await self.manager.async_add_job(
            job_type="timer",
            target_action="timer",
            delay_seconds=600.0,
            name="pizza",
        )
        self.assertEqual(job.job_type, "timer")
        self.assertEqual(job.name, "pizza")

        timers = self.manager.list_jobs(job_type="timer")
        self.assertEqual(len(timers), 1)
        self.assertEqual(timers[0].name, "pizza")

    async def test_change_timer_time(self):
        job = await self.manager.async_add_job(
            job_type="timer",
            target_action="timer",
            delay_seconds=300.0,
            name="massa",
        )
        initial_due = job.due_timestamp
        # Add 120 seconds
        updated = await self.manager.async_change_timer(name="massa", delta_seconds=120.0)
        self.assertIsNotNone(updated)
        self.assertAlmostEqual(updated.due_timestamp - initial_due, 120.0, delta=1.0)

    async def test_cancel_jobs(self):
        await self.manager.async_add_job(job_type="alarm", target_action="clock_alarm", time_str="06:30", name="remedio")
        await self.manager.async_add_job(job_type="alarm", target_action="clock_alarm", time_str="08:00", name="treino")

        # Cancel one by name
        cancelled = await self.manager.async_cancel_jobs(job_type="alarm", name="remedio")
        self.assertEqual(len(cancelled), 1)
        self.assertEqual(cancelled[0].name, "remedio")

        active = self.manager.list_jobs(job_type="alarm")
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].name, "treino")

    async def test_snooze_and_dismiss_alarm(self):
        job = await self.manager.async_add_job(
            job_type="alarm",
            target_action="clock_alarm",
            delay_seconds=1.0,
            name="acorda",
        )
        # Snooze for 300s
        snoozed = await self.manager.async_snooze_job(job_type="alarm", duration_seconds=300.0)
        self.assertIsNotNone(snoozed)
        self.assertEqual(snoozed.status, "snoozed")
        self.assertGreater(snoozed.due_timestamp, time.time() + 290.0)

        # Dismiss
        dismissed = await self.manager.async_dismiss_job(job_type="alarm")
        self.assertIsNotNone(dismissed)
        self.assertEqual(dismissed.status, "completed")
        self.assertEqual(len(self.manager.list_jobs(job_type="alarm")), 0)

    async def test_quota_limit(self):
        for i in range(MAX_ACTIVE_JOBS):
            await self.manager.async_add_job(
                job_type="reminder",
                target_action="reminder",
                delay_seconds=1000.0 + i,
                title=f"Lembrete {i}",
            )
        self.assertEqual(self.manager.count_active(), MAX_ACTIVE_JOBS)
        with self.assertRaises(ValueError) as ctx:
            await self.manager.async_add_job(
                job_type="reminder",
                target_action="reminder",
                delay_seconds=5000.0,
                title="Excedente",
            )
        self.assertEqual(str(ctx.exception), "quota_exceeded")


if __name__ == "__main__":
    unittest.main()
