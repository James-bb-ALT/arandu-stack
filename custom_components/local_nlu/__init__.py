"""Local NLU Home Assistant companion integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


async def async_setup(_: HomeAssistant, __: dict[str, Any]) -> bool:
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    from .client import ClientError, LocalNluClient, normalize_endpoint
    from .const import CONF_CONTEXTUAL_ENABLED, CONF_ENDPOINT, CONF_SHADOW_ENABLED, CONF_V2_ENABLED, PLATFORMS
    from .runtime import LocalNluRuntime
    from .endpoint_discovery import prepare_entry
    from homeassistant.exceptions import ConfigEntryNotReady

    try:
        endpoint = await prepare_entry(hass, entry, async_get_clientsession(hass))
    except (KeyError, ClientError) as exc:
        raise ConfigEntryNotReady('Não foi possível localizar o NLU do Arandu Assistant; aguardando o app e o Supervisor.') from exc
    client = LocalNluClient(async_get_clientsession(hass), endpoint)
    entry.runtime_data = LocalNluRuntime(
        hass,
        client,
        lambda: bool(entry.options.get(CONF_V2_ENABLED, False)),
        lambda: bool(entry.options.get(CONF_SHADOW_ENABLED, False)),
        lambda: bool(entry.options.get(CONF_CONTEXTUAL_ENABLED, True)),
        lambda: {**dict(entry.options), **getattr(entry.runtime_data, '_stack_options', {})},
    )
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    from .ai_lab import async_register
    await async_register(hass)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    from .client import normalize_endpoint
    from .const import CONF_ENDPOINT
    value = entry.options.get(CONF_ENDPOINT, entry.data.get(CONF_ENDPOINT, ''))
    if not value:
        await hass.config_entries.async_reload(entry.entry_id)
        return
    selected_endpoint = normalize_endpoint(value)
    if selected_endpoint != entry.runtime_data._client._endpoint:
        await hass.config_entries.async_reload(entry.entry_id)
        return
    # Clearing dialogue on every saved options change prevents change/restore replay.
    # Runtime options are read live; no restart or automatic add-on update is needed.
    runtime = entry.runtime_data
    runtime._stack_options = {}
    runtime._stack_policy = {}
    if runtime._contextual is not None:
        runtime._contextual.options_changed()
    runtime._pending.clear()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    from .const import PLATFORMS

    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        if getattr(entry.runtime_data, "_contextual", None) is not None:
            entry.runtime_data._contextual.close()
        try:
            del entry.runtime_data
        except AttributeError:
            pass
    return unloaded
