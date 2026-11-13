"""Short misunderstanding replies for registered ReSpeaker origins only."""
import re
from urllib.parse import urlsplit


FAILURES = frozenset({'missing_slot', 'no_match', 'unsupported_intent',
                      'ambiguous', 'unresolved_target'})


def short_failure_origin(hass, user_input):
    from homeassistant.helpers import device_registry, entity_registry

    satellite_id = getattr(user_input, 'satellite_id', None)
    if satellite_id is not None:
        entry = entity_registry.async_get(hass).entities.get(satellite_id)
        if entry is None or not satellite_id.startswith('assist_satellite.'):
            return False
        device_id = getattr(entry, 'device_id', None)
    else:
        device_id = getattr(user_input, 'device_id', None)
    device = device_registry.async_get(hass).async_get(device_id) if device_id else None
    if device is None:
        return False
    labels = [getattr(device, key, None) for key in ('model', 'model_id', 'name', 'name_by_user')]
    # The installed satellite is named Arandu in HA; its registered hostname
    # still identifies respeaker-jaime.local. Never infer RAM from ESPHome alone.
    url = getattr(device, 'configuration_url', None)
    if isinstance(url, str):
        try:
            labels.append(urlsplit(url).hostname)
        except ValueError:
            pass
    return any('respeaker' in re.sub(r'[^a-z0-9]', '', label.casefold())
               for label in labels if isinstance(label, str))
