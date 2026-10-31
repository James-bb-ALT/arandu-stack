"""Local dialogue/output controls; never resolve another room or media device."""
import re

from .contextual_protocol import normalize

_CANCEL = ('cancela', 'cancela isso', 'cancelar', 'para', 'pare', 'deixa pra la',
           'deixa para la', 'esquece', 'nada', 'nao faz nada', 'nao faca nada')
_DIGITS = dict(zip(('zero', 'um', 'dois', 'tres', 'quatro', 'cinco', 'seis', 'sete',
                   'oito', 'nove', 'dez'), range(11)))
_DIGITS.update(uma=1, duas=2, onze=11, doze=12, treze=13, quatorze=14, catorze=14,
               quinze=15, dezesseis=16, dezessete=17, dezoito=18, dezenove=19,
               vinte=20, trinta=30, quarenta=40, cinquenta=50, sessenta=60,
               setenta=70, oitenta=80, noventa=90, cem=100)


def cancellation(text):
    text = normalize(text)
    return any(text == phrase or text.endswith(' ' + phrase) for phrase in _CANCEL)


def volume(text):
    """Return (recognized, percent). A bare numeric value uses the 0–10 scale."""
    invalid_number = bool(re.search(r'[+-]\s*\d|\d[.,]\d', text))
    text = normalize(text.replace('%', ' por cento '))
    match = re.fullmatch(
        r'(?:(?:coloca|coloque|ajusta|ajuste|bota|deixa) (?:o )?)?'
        r'(?:seu )?volume(?: (?:daqui|aqui))?(?: (?:em|para|no))? (.+)', text)
    if match is None:
        return False, None
    if invalid_number:
        return True, None
    raw = match[1]
    percent = raw.endswith(' por cento')
    raw = raw.removesuffix(' por cento')
    if raw.isascii() and raw.isdigit():
        value = int(raw)
    elif raw in _DIGITS:
        value = _DIGITS[raw]
    elif ' e ' in raw:
        tens, units = raw.split(' e ', 1)
        value = _DIGITS.get(tens, -1000) + _DIGITS.get(units, -1000)
        if _DIGITS.get(tens, 0) not in range(20, 100, 10) or _DIGITS.get(units, 0) not in range(1, 10):
            value = -1
    else:
        # Explicit targets, e.g. "volume da TV", belong to the normal grammar.
        return False, None
    if not 0 <= value <= (100 if percent else 10):
        return True, None
    return True, value if percent else value * 10


def origin_player(hass, user_input, snapshot, action, options=None):
    """Require one capable, authorized output on the origin's exact device."""
    from homeassistant.helpers import entity_registry
    registry = entity_registry.async_get(hass)
    bindings = (options or {}).get('voice_output_bindings', {})
    if type(bindings) is not dict or len(bindings) > 128:
        return None
    satellite_id = getattr(user_input, 'satellite_id', None)
    satellite = registry.entities.get(satellite_id) if isinstance(satellite_id, str) else None
    if satellite_id is not None:
        if (satellite is None or not satellite_id.startswith('assist_satellite.')
                or getattr(satellite, 'disabled_by', None) is not None):
            return None
        device_id = getattr(satellite, 'device_id', None)
    else:
        device_id = getattr(user_input, 'device_id', None)
    for kind, origin in (('satellite', satellite_id), ('device', getattr(user_input, 'device_id', None))):
        if origin is not None and kind + ':' + origin in bindings:
            target = bindings[kind + ':' + origin]
            return next((key for key, row in snapshot.by_id.items()
                         if row['entity_id'] == target and row['domain'] == 'media_player'
                         and action in row['actions']), None)
    if not device_id:
        return None
    found = []
    for key, row in snapshot.by_id.items():
        entry = registry.entities.get(row['entity_id'])
        if (row['domain'] == 'media_player' and action in row['actions']
                and entry is not None and getattr(entry, 'device_id', None) == device_id):
            found.append(key)
    return found[0] if len(found) == 1 else None
