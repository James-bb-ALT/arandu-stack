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
    endpoint = entry.options.get('stack_endpoint', entry.data.get('stack_endpoint', ''))
    if not endpoint:
        return user_input, None
    from .ai_lab import local_request
    from homeassistant.helpers.aiohttp_client import async_get_clientsession
    try:
        state = await local_request(async_get_clientsession(hass), endpoint, '/runtime', timeout=3)
        if state.get('nlu_enabled') is not True:
            return user_input, RuntimeResult(code='unavailable', response_text='O módulo NLU está desativado no Arandu Stack.')
        rows = state.get('phrases', [])
        if not isinstance(rows, list) or len(rows) > 200:
            raise ValueError('Configuração de frases inválida')
        rewritten = expand(user_input.text, rows)
        if rewritten == user_input.text:
            return user_input, None
        if not isinstance(rewritten, str) or not 1 <= len(rewritten) <= 512 or any(ord(c) < 32 for c in rewritten):
            raise ValueError('Comando personalizado inválido')
        return replace(user_input, text=rewritten), None
    except Exception:
        return user_input, RuntimeResult(code='unavailable', response_text='Não consegui consultar o Arandu Stack. Confira a conexão antes de tentar novamente.')
