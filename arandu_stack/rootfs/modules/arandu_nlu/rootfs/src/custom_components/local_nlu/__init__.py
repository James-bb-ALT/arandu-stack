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
        lambda: {"stack_endpoint": entry.data.get("stack_endpoint", ""), **dict(entry.options), **getattr(entry.runtime_data, '_stack_options', {})},
    )
    # Restore scheduling at setup, even before the first voice request.
    from .contextual_runtime import ContextualRuntime
    runtime = entry.runtime_data
    runtime._contextual = ContextualRuntime(hass,client,runtime._contextual_options)
    try:
        await runtime._contextual.scheduler.async_load()
    except Exception as exc:
        await runtime._contextual.async_close()
        raise ConfigEntryNotReady('Não foi possível restaurar os agendamentos com segurança.') from exc
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
    if runtime._contextual is not None:
        from .workload import bounded
        contextual = runtime._contextual
        desired = (bounded(entry.options,'performance_concurrency',4,1,8), bounded(entry.options,'performance_waiting',16,0,32),
                   bounded(entry.options,'performance_model_waiting',4,0,8),bounded(entry.options,'performance_timeout',45,5,90),
                   bounded(entry.options,'performance_scheduler_concurrency',2,1,4),
                   bounded(entry.options,'performance_scheduler_timeout',20,5,60),bounded(entry.options,'performance_scheduler_jobs',50,1,50))
        actual = (contextual.nlu_admission.concurrency,contextual.nlu_admission.waiting,contextual.model_admission.waiting,contextual.total_timeout,
                  contextual.scheduler.concurrency,contextual.scheduler.deadline,contextual.scheduler.max_active)
        if desired != actual:
            await hass.config_entries.async_reload(entry.entry_id)
            return
    runtime._stack_options = {}
    runtime._stack_policy = {}
    if runtime._contextual is not None:
        runtime._contextual.options_changed()
    runtime._pending.clear()
    from .announcement_router import clear_session_store
    clear_session_store(hass, entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    from .const import PLATFORMS

    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        from .announcement_router import clear_session_store
        clear_session_store(hass, entry.entry_id)
        if publisher := getattr(entry.runtime_data, '_trace_publisher', None):
            publisher.close()
        if getattr(entry.runtime_data, "_contextual", None) is not None:
            await entry.runtime_data._contextual.async_close()
        try:
            del entry.runtime_data
        except AttributeError:
            pass
    return unloaded
