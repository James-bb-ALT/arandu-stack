"""Publish one combined Wyoming service, without sharing controller credentials."""
import asyncio
import json
import os

from aiohttp import ClientSession, ClientTimeout
from wyoming_gateway import BACKENDS, PORT, describe_backend

KINDS = {key: kind for key, (_, kind) in BACKENDS.items()}


async def ready(port, kind):
    return bool((await describe_backend(port)).get(kind))


async def announce_stack(gateway, journal):
    token = os.environ.get('SUPERVISOR_TOKEN')
    if not token:
        journal.event('app', 'Sem Supervisor: conecte Wyoming manualmente à porta 10212 para STT, TTS e Wake.')
        return
    published = None
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
