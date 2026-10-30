"""Publish one combined Wyoming service, without sharing controller credentials."""
import asyncio
import json
import os

from aiohttp import ClientSession, ClientTimeout
from wyoming_gateway import BACKENDS, PORT, describe_backend

KINDS = {key: kind for key, (_, kind) in BACKENDS.items()}


async def nlu_ready():
    # Separate session: Supervisor Authorization must never reach the engine.
    try:
        async with ClientSession(timeout=ClientTimeout(total=3), trust_env=False) as session:
            async with session.get('http://127.0.0.1:11555/health', allow_redirects=False) as response:
                raw = await response.content.read(1025)
                return response.status == 200 and len(raw) <= 1024 and json.loads(raw) == {'status': 'ok', 'version': 1}
    except Exception:
        return False


async def ready(port, kind):
    return bool((await describe_backend(port)).get(kind))


async def announce_stack(gateway, journal):
    token = os.environ.get('SUPERVISOR_TOKEN')
    if not token:
        journal.event('app', 'Sem Supervisor: conecte Wyoming manualmente à porta 10212 para STT, TTS e Wake.')
        return
    published = None
    nlu_published = False
    failures = 0
    async with ClientSession(headers={'Authorization': 'Bearer ' + token}, timeout=ClientTimeout(total=10)) as session:
        async def api(path, payload=None):
            async with session.request('GET' if payload is None else 'POST', 'http://supervisor' + path, json=payload) as response:
                response.raise_for_status()
                body = await response.json()
                if body.get('result') != 'ok':
                    raise ValueError('Supervisor recusou o anúncio Wyoming')
                return body.get('data', {})

        while True:
            if not nlu_published and await nlu_ready():
                try:
                    info = await api('/addons/self/info')
                    host = info.get('hostname')
                    if not isinstance(host, str) or not host:
                        raise ValueError('Supervisor não informou o hostname do app')
                    await api('/discovery', {'service': 'local_nlu', 'config': {'host': host.replace('_', '-')}})
                    nlu_published = True
                    journal.event('nlu', 'Arandu Assistant anunciado ao Home Assistant; configure a integração descoberta. Entradas existentes são preservadas.')
                except Exception as exc:
                    if failures % 6 == 0:
                        journal.event('nlu', 'Falha na descoberta do Arandu Assistant; nova tentativa automática: ' + str(exc), error=True)
                    failures += 1
            data = await gateway.info()
            waiting = [key for key in BACKENDS if gateway.running(key) and not data.get(KINDS[key])]
            signature = json.dumps(data, sort_keys=True)
            if data and not waiting and signature != published:
                try:
                    info = await api('/addons/self/info')
                    host = info.get('hostname')
                    if not isinstance(host, str) or not host:
                        raise ValueError('Supervisor não informou o hostname do app')
                    uri = f'tcp://{host.replace("_", "-")}:{PORT}'
                    await api('/discovery', {'service': 'wyoming', 'config': {'uri': uri}})
                    changed = published is not None
                    published = signature
                    for key, kind in KINDS.items():
                        if data.get(kind):
                            journal.event(key, 'Wyoming anunciado ao Home Assistant: ' + uri + ' (integração conjunta STT/TTS/Wake).')
                    journal.event('app', 'Recursos Wyoming atualizados; recarregue a integração existente para adicionar plataformas.' if changed else 'Configure a integração Wyoming descoberta. Se já existia na porta 10212, recarregue-a para incluir STT e Wake.')
                except Exception as exc:
                    if failures % 6 == 0:
                        journal.event('app', 'Falha na descoberta Wyoming; nova tentativa automática: ' + str(exc), error=True)
                    failures += 1
            await asyncio.sleep(10)
