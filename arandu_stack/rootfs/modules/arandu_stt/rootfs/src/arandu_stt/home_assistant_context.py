"""Periodic background reconstruction through the existing Supervisor inventory API."""
import asyncio
import logging
from inventory_sync import sync_inventory

LOG = logging.getLogger('arandu.context')


async def refresh_loop(resolver, interval, fetch=sync_inventory, query_ha=True):
    while True:
        await asyncio.sleep(interval)
        try:
            if query_ha:
                inventory = await fetch(resolver.inventory_path)
                await asyncio.to_thread(resolver.refresh, inventory)
            else:
                await asyncio.to_thread(resolver.refresh)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Only the exception type: connection errors can contain credentials in URLs.
            LOG.warning('Contexto HA indisponível (%s); mantendo snapshot anterior', type(exc).__name__)
