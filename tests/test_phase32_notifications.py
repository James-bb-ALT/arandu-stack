"""Tests for Phase 3.2 alarm, reminder, and timer delivery notifications."""
import unittest
from unittest.mock import AsyncMock, Mock

from custom_components.local_nlu.scheduled_delivery import ScheduledDeliveryHandler
from custom_components.local_nlu.scheduled_jobs import ScheduledJob


class TestScheduledDelivery(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass = Mock()
        self.hass.services = Mock()
        self.hass.services.async_call = AsyncMock()
        self.hass.services.has_service = Mock(return_value=True)
        self.hass.states = Mock()
        self.hass.states.get = Mock(return_value=Mock(state="idle"))
        self.options = {
            "delivery_media_player": "media_player.caixa_som",
            "delivery_tts_engine": "tts.google_translate",
        }
        self.handler = ScheduledDeliveryHandler(self.hass, lambda: self.options)

    async def test_deliver_alarm(self):
        job = ScheduledJob(
            id="job_alarm_1",
            job_type="alarm",
            target_action="clock_alarm",
            name="trabalho",
            due_iso="2026-11-11T07:00:00Z",
        )
        await self.handler.async_deliver_notification(job)

        # 1. Verify persistent notification call
        self.hass.services.async_call.assert_any_call(
            "persistent_notification",
            "create",
            {
                "notification_id": "local_nlu_job_alarm_1",
                "title": "Despertador: trabalho",
                "message": "Seu despertador chamado trabalho está tocando!",
            },
            blocking=True,
        )

        # 2. Verify TTS speak announcement
        self.hass.services.async_call.assert_any_call(
            "tts",
            "speak",
            {
                "entity_id": "tts.google_translate",
                "media_player_entity_id": "media_player.caixa_som",
                "message": "Seu despertador chamado trabalho está tocando!",
            },
            blocking=False,
        )

    async def test_deliver_reminder(self):
        job = ScheduledJob(
            id="job_rem_1",
            job_type="reminder",
            target_action="reminder",
            title="tirar o bolo do forno",
        )
        await self.handler.async_deliver_notification(job)

        self.hass.services.async_call.assert_any_call(
            "persistent_notification",
            "create",
            {
                "notification_id": "local_nlu_job_rem_1",
                "title": "Lembrete Arandu",
                "message": "Lembrete: tirar o bolo do forno",
            },
            blocking=True,
        )

    async def test_deliver_timer(self):
        job = ScheduledJob(
            id="job_timer_1",
            job_type="timer",
            target_action="timer",
            name="arroz",
        )
        await self.handler.async_deliver_notification(job)

        self.hass.services.async_call.assert_any_call(
            "persistent_notification",
            "create",
            {
                "notification_id": "local_nlu_job_timer_1",
                "title": "Timer: arroz",
                "message": "O tempo do timer do arroz acabou!",
            },
            blocking=True,
        )


if __name__ == "__main__":
    unittest.main()
