"""Announce ready Wyoming modules from the trusted controller only."""
import asyncio
import json
import os
from contextlib import suppress

from aiohttp import ClientSession, ClientTimeout

KINDS = {'stt': 'asr', 'tts': 'tts', 'wake': 'wake'}


async def ready(port, kind):
    writer = None
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection('127.0.0.1', port), 2)
        writer.write(b'{"type":"describe"}\n')
        await writer.drain()
        header = json.loads(await asyncio.wait_for(reader.readline(), 3))
        if not isinstance(header, dict):
            return False
        length = header.get('data_length', 0)
        if type(length) is not int or not 0 <= length <= 262144:
            return False
        data = header.get('data', {})
        if length:
            data = json.loads(await asyncio.wait_for(reader.readexactly(length), 3))
        return header.get('type') == 'info' and isinstance(data, dict) and bool(data.get(kind))
    except (OSError, ValueError, TimeoutError, asyncio.IncompleteReadError):
        return False
    finally:
        if writer:
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()


async def announce(key, port, process, journal):
    token = os.environ.get('SUPERVISOR_TOKEN')
    if not token:
        journal.event(key, 'Descoberta Wyoming indisponível fora do Supervisor; use conexão manual.')
        return
    failures = 0
    async with ClientSession(headers={'Authorization': 'Bearer ' + token}, timeout=ClientTimeout(total=10)) as session:
        async def api(path, payload=None):
            async with session.request('GET' if payload is None else 'POST', 'http://supervisor' + path, json=payload) as response:
                response.raise_for_status()
                body = await response.json()
                if body.get('result') != 'ok':
                    raise ValueError('Supervisor recusou o anúncio Wyoming')
                return body.get('data', {})

        while process.returncode is None:
            if await ready(port, KINDS[key]):
                if process.returncode is not None:
                    return
                try:
                    info = await api('/addons/self/info')
                    host = info.get('hostname')
                    if not isinstance(host, str) or not host:
                        raise ValueError('Supervisor não informou o hostname do app')
                    uri = f'tcp://{host.replace("_", "-")}:{port}'
                    # Supervisor keys discovery by app + service, ignoring config.
                    # Publishing each module would overwrite the previous endpoint.
                    if key == 'tts':
                        await api('/discovery', {'service': 'wyoming', 'config': {'uri': uri}})
                        journal.event(key, 'Wyoming anunciado ao Home Assistant: ' + uri + '. Configure a integração descoberta em Dispositivos e serviços.')
                    else:
                        journal.event(key, 'Wyoming pronto: ' + uri + '. Adicione Wyoming Protocol manualmente em Dispositivos e serviços; a descoberta automática deste app é reservada ao TTS.')
                    return
                except Exception as exc:
                    if failures % 6 == 0:
                        journal.event(key, 'Falha na descoberta Wyoming; nova tentativa automática: ' + str(exc), error=True)
                    failures += 1
            await asyncio.sleep(10)
