"""Optional Stack phrase expansion; preserves HA caller/context and live execution checks."""
from dataclasses import replace
import unicodedata
from .runtime import RuntimeResult

def normalize(text):
    return ' '.join(unicodedata.normalize('NFKC', text).casefold().split())

def expand(text, rows):
    key = normalize(text)
    matches = [row['command'] for row in rows if row.get('enabled') is True and normalize(row['phrase']) == key]
    if len(matches) > 1:
        raise ValueError('Frase duplicada')
    return matches[0] if matches else text

async def prepare(hass, entry, user_input):
    from .voice_controls import cancellation
    if type(getattr(user_input, 'text', None)) is str and cancellation(user_input.text):
        # Never let remote phrase expansion or an unavailable NLU delay cancellation.
        return user_input, None
    endpoint = entry.options.get('stack_endpoint', entry.data.get('stack_endpoint', ''))
    if not endpoint:
        entry.runtime_data._stack_options = {}
        entry.runtime_data._stack_policy = {}
        return user_input, None
    from .ai_lab import local_request
    from homeassistant.helpers.aiohttp_client import async_get_clientsession
    try:
        state = await local_request(async_get_clientsession(hass), endpoint, '/runtime', timeout=3)
        if state.get('nlu_enabled') is not True:
            return user_input, RuntimeResult(code='unavailable', response_text='O módulo NLU está desativado no Arandu Stack.')
        options = state.get('nlu_options', {})
        if not isinstance(options, dict):
            raise ValueError('Configuração NLU inválida')
        limit = options.get('max_input_chars', 512)
        if type(limit) is not int or not 64 <= limit <= 512:
            raise ValueError('Limite NLU inválido')
        if len(user_input.text) > limit:
            return user_input, RuntimeResult(code='invalid_request', response_text='Esse comando ultrapassa o limite de caracteres configurado.')
        runtime_options = {k: options[k] for k in ('session_ttl', 'low_battery_threshold') if k in options}
        if any(type(v) is not int for v in runtime_options.values()) or not 1 <= runtime_options.get('session_ttl', 60) <= 600 or not 0 <= runtime_options.get('low_battery_threshold', 20) <= 100:
            raise ValueError('Política NLU inválida')
        runtime = entry.runtime_data
        if options != getattr(runtime, '_stack_policy', {}):
            runtime._stack_policy = dict(options)
            runtime._stack_options = runtime_options
            if runtime._contextual is not None:
                runtime._contextual.options_changed()
            runtime._pending.clear()
        if options.get('custom_phrases_enabled', True) is not True:
            return user_input, None
        rows = state.get('phrases', [])
        if not isinstance(rows, list) or len(rows) > 200:
            raise ValueError('Configuração de frases inválida')
        from .voice_controls import volume
        if volume(user_input.text)[0]:
            return user_input, None
        rewritten = expand(user_input.text, rows)
        if rewritten == user_input.text:
            return user_input, None
        if isinstance(rewritten, str) and len(rewritten) > limit:
            return user_input, RuntimeResult(code='invalid_request', response_text='O comando da frase personalizada ultrapassa o limite de caracteres configurado.')
        if not isinstance(rewritten, str) or not 1 <= len(rewritten) <= limit or any(ord(c) < 32 for c in rewritten):
            raise ValueError('Comando personalizado inválido')
        return replace(user_input, text=rewritten), None
    except Exception:
        return user_input, RuntimeResult(code='unavailable', response_text='Não consegui consultar o Arandu Stack. Confira a conexão antes de tentar novamente.')
