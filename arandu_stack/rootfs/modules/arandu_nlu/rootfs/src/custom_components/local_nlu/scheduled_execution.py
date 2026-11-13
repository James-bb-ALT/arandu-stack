"""Scheduled effects enter the same authorized commit boundary as live speech."""
from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)


class ScheduledExecutionHandler:
    def __init__(self, hass: Any, options: Any = None, executor: Any = None) -> None:
        self.hass = hass
        self._options = options if options is not None else lambda: {}
        self._executor = executor

    async def async_execute_scheduled_action(self, job: Any) -> bool:
        if self._executor is None:
            # Legacy jobs cannot acquire an invented user at delivery time.
            _LOGGER.warning('Scheduled action has no authorized execution boundary')
            return False
        return await self._executor(job)
