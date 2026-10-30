"""Resolve the installed Stack through Supervisor metadata, never guessed DNS."""
from urllib.parse import urlsplit

from .client import ClientError, LocalNluClient, normalize_endpoint


def from_addons(addons):
    candidates = set()
    for slug, info in addons.items():
        if not isinstance(slug, str) or not isinstance(info, dict):
            continue
        if not slug.endswith('_arandu_stack'):
            continue
        host = info.get('hostname') or slug.replace('_', '-')
        try:
            endpoint = normalize_endpoint(f'http://{host}:11555')
            panel = normalize_endpoint(f'http://{host}:8099')
        except ClientError:
            continue
        candidates.add((endpoint, panel))
    if len(candidates) != 1:
        raise ClientError('discovery_ambiguous' if candidates else 'discovery_missing')
    return next(iter(candidates))


def installed_endpoints(hass):
    try:
        from homeassistant.components.hassio import get_addons_info
        return from_addons(get_addons_info(hass))
    except ClientError:
        raise
    except Exception as exc:
        raise ClientError('supervisor_not_ready') from exc


async def resolve_endpoints(hass, session, endpoint='', panel=''):
    selected = endpoint.strip() if isinstance(endpoint, str) else ''
    legacy = False
    if selected:
        selected = normalize_endpoint(selected)
        host = urlsplit(selected).hostname
        legacy = host == 'local-ptbr-nlu' or host.endswith('-ptbr-nlu')
        if not legacy:
            discovered_panel = normalize_endpoint(f'http://{host}:8099') if host.endswith('-arandu-stack') else ''
            return selected, normalize_endpoint(panel) if panel else discovered_panel, False
        try:
            await LocalNluClient(session, selected).async_health()
            return selected, normalize_endpoint(panel) if panel else '', False
        except ClientError:
            pass
    selected, discovered_panel = installed_endpoints(hass)
    await LocalNluClient(session, selected).async_health()
    return selected, normalize_endpoint(panel) if panel else discovered_panel, True


async def prepare_entry(hass, entry, session):
    data, options = dict(entry.data), dict(entry.options)
    original = {name: {'data': data.get(name), 'options': options.get(name)}
                for name in ('endpoint', 'stack_endpoint')}
    endpoint, panel, automatic = await resolve_endpoints(
        hass, session, options.get('endpoint', data.get('endpoint', '')),
        options.get('stack_endpoint', data.get('stack_endpoint', '')))
    if automatic:
        data.setdefault('endpoint_migration_backup', original)
    data.update(endpoint=endpoint, stack_endpoint=panel)
    for name, value in (('endpoint', endpoint), ('stack_endpoint', panel)):
        if name in options:
            options[name] = value
    title = 'Arandu Assistant' if entry.title in {'Arandu NLU', 'ARANDU NLU', 'Local NLU'} else entry.title
    if data != entry.data or options != entry.options or title != entry.title:
        hass.config_entries.async_update_entry(entry, data=data, options=options, title=title)
    return endpoint
