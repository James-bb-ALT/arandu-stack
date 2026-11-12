"""Re-validated execution of scheduled device actions."""
from __future__ import annotations

import logging
from typing import Any

from .capabilities import ADAPTERS

try:
    from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
except ImportError:
    STATE_UNAVAILABLE = "unavailable"
    STATE_UNKNOWN = "unknown"

_LOGGER = logging.getLogger(__name__)


class ScheduledExecutionHandler:
    def __init__(self, hass: Any, options: Any = None) -> None:
        self.hass = hass
        self._options = options if options is not None else lambda: {}

    async def async_execute_scheduled_action(self, job: Any) -> bool:
        """Re-validate state and execute the scheduled operation."""
        action = job.target_action or job.parameters.get("target_action", "turn_on")
        targets = job.targets
        if not targets:
            _LOGGER.warning("Scheduled job %s has no targets to execute", job.id)
            return False

        all_ok = True
        for entity_id in targets:
            state = self.hass.states.get(entity_id)
            if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                _LOGGER.warning("Scheduled action target %s is unavailable at execution time", entity_id)
                all_ok = False
                continue

            domain = entity_id.split(".", 1)[0]
            adapter = ADAPTERS.get((domain, action))
            if not adapter or not adapter.service:
                _LOGGER.warning("No adapter service found for domain %s action %s", domain, action)
                all_ok = False
                continue

            try:
                await self.hass.services.async_call(
                    domain,
                    adapter.service,
                    {"entity_id": entity_id},
                    blocking=True,
                )
            except Exception as err:
                _LOGGER.error("Failed to execute scheduled action %s on %s: %s", action, entity_id, err)
                all_ok = False

        return all_ok
