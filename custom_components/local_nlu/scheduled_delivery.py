"""Notification delivery for alarms, reminders, and timers via TTS and persistent notifications."""
from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)


class ScheduledDeliveryHandler:
    def __init__(self, hass: Any, options: Any = None) -> None:
        self.hass = hass
        self._options = options if options is not None else lambda: {}

    async def async_deliver_notification(self, job: Any) -> bool:
        """Deliver alarm, reminder, or timer notification honestly and reliably."""
        opts = self._options() if callable(self._options) else self._options
        target_player = opts.get("delivery_media_player")
        target_tts = opts.get("delivery_tts_engine")

        message = self._compose_message(job)
        title = self._compose_title(job)
        acknowledged = False
        job.notification_status,job.tts_status = 'unavailable','disabled'

        # 1. Persistent notification fallback (always guarantees visible delivery)
        try:
            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "notification_id": f"local_nlu_{job.id}",
                    "title": title,
                    "message": message,
                },
                blocking=True,
            )
            acknowledged = True
            job.notification_status = 'accepted'
        except Exception as err:
            job.notification_status = 'error'
            _LOGGER.warning('Scheduled notification failed')

        # 2. TTS Voice announcement on configured/available media player
        if target_player and target_tts and self.hass.states.get(target_player):
            try:
                tts_engine = target_tts
                if self.hass.services.has_service("tts", "speak"):
                    await self.hass.services.async_call(
                        "tts",
                        "speak",
                        {
                            "entity_id": tts_engine if "." in tts_engine else f"tts.{tts_engine}",
                            "media_player_entity_id": target_player,
                            "message": message,
                        },
                        blocking=False,
                    )
                    acknowledged = True
                    job.tts_status = 'accepted_not_observed'
            except Exception as err:
                job.tts_status = 'error'
                _LOGGER.warning('Scheduled TTS delivery failed')
        return acknowledged

    def _compose_title(self, job: Any) -> str:
        if job.job_type == "alarm":
            return f"Despertador: {job.name}" if job.name else "Despertador"
        if job.job_type == "reminder":
            return "Lembrete Arandu"
        if job.job_type == "timer":
            return f"Timer: {job.name}" if job.name else "Timer finalizado"
        return "Notificação Arandu"

    def _compose_message(self, job: Any) -> str:
        if job.job_type == "alarm":
            name = f" chamado {job.name}" if job.name else ""
            return f"Seu despertador{name} está tocando!"
        if job.job_type == "reminder":
            t = job.title or (job.parameters.get("title") if job.parameters else None) or "você tem um lembrete pendente"
            return f"Lembrete: {t}"
        if job.job_type == "timer":
            name = f" do {job.name}" if job.name else ""
            return f"O tempo do timer{name} acabou!"
        return f"Ação agendada executada."
