"""One Wyoming endpoint exposes independently enabled speech modules."""
import asyncio
from contextlib import suppress
import json

PORT = 10212  # Preserve the existing TTS integration address.
BACKENDS = {'stt': (10350, 'asr'), 'tts': (10213, 'tts'), 'wake': (10400, 'wake')}
ROUTES = {'transcribe': 'stt', 'audio-start': 'stt', 'synthesize': 'tts',
          'synthesize-start': 'tts', 'detect': 'wake'}


async def read_frame(reader):
    line = await reader.readline()
    if not line:
        return None
    header = json.loads(line)
    if not isinstance(header, dict) or not isinstance(header.get('type'), str):
        raise ValueError('Cabeçalho Wyoming inválido')
    lengths = [header.get('data_length', 0), header.get('payload_length', 0)]
    if any(type(n) is not int or n < 0 for n in lengths) or lengths[0] > 1048576 or lengths[1] > 16777216:
        raise ValueError('Evento Wyoming excede o limite')
    data_bytes = await reader.readexactly(lengths[0]) if lengths[0] else b''
    payload = await reader.readexactly(lengths[1]) if lengths[1] else b''
    data = dict(header.get('data') or {})
    if data_bytes:
        data.update(json.loads(data_bytes))
    return header['type'], data, line + data_bytes + payload


async def close_writer(writer):
    if writer:
        writer.close()
        with suppress(OSError):
            await writer.wait_closed()


async def describe_backend(port):
    writer = None
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection('127.0.0.1', port), 2)
        writer.write(b'{"type":"describe"}\n')
        await writer.drain()
        frame = await asyncio.wait_for(read_frame(reader), 3)
        return frame[1] if frame and frame[0] == 'info' else {}
    except (OSError, ValueError, TypeError, TimeoutError, asyncio.IncompleteReadError):
        return {}
    finally:
        await close_writer(writer)


def encode_event(kind, data):
    raw = json.dumps(data, ensure_ascii=False).encode('utf-8')
    return json.dumps({'type': kind, 'data_length': len(raw)}).encode() + b'\n' + raw


class WyomingGateway:
    def __init__(self, manager, journal):
        self.manager, self.journal = manager, journal
        self.server = None
        self.clients = set()

    def running(self, key):
        process = self.manager.processes.get(key)
        return bool(process and process.returncode is None)

    async def info(self):
        keys = [key for key in BACKENDS if self.running(key)]
        results = await asyncio.gather(*(describe_backend(BACKENDS[key][0]) for key in keys))
        return {BACKENDS[key][1]: data[BACKENDS[key][1]] for key, data in zip(keys, results)
                if data.get(BACKENDS[key][1]) and self.running(key)}

    async def description(self):
        data = await self.info()
        if not data or any(self.running(key) and not data.get(kind) for key, (_, kind) in BACKENDS.items()):
            # Core creates entity platforms only during setup. An empty or partial
            # Info would silently omit STT until the integration is reloaded.
            raise ValueError('Módulos Wyoming ainda não estão prontos; aguarde a inicialização')
        return data

    async def start(self):
        self.server = await asyncio.start_server(self.handle, '0.0.0.0', PORT)
        self.journal.event('app', 'Wyoming conjunto na porta 10212: STT, TTS e Wake ativos em uma integração.')

    async def close(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        for task in list(self.clients):
            task.cancel()
        await asyncio.gather(*list(self.clients), return_exceptions=True)

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.clients.add(task)
        backend_writer = None
        pumps = []
        try:
            while frame := await asyncio.wait_for(read_frame(reader), 60):
                kind, _, raw = frame
                if kind == 'describe':
                    writer.write(encode_event('info', await self.description()))
                    await writer.drain()
                    continue
                key = ROUTES.get(kind)
                if key is None or not self.running(key):
                    raise ValueError('Módulo Wyoming indisponível para ' + kind)
                backend_reader, backend_writer = await asyncio.wait_for(asyncio.open_connection('127.0.0.1', BACKENDS[key][0]), 3)
                backend_writer.write(raw)
                await backend_writer.drain()

                async def copy(source, target):
                    while chunk := await source.read(65536):
                        target.write(chunk)
                        await target.drain()
                pumps = [asyncio.create_task(copy(reader, backend_writer)),
                         asyncio.create_task(copy(backend_reader, writer))]
                done, _ = await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
                for completed in done:
                    completed.result()
                return
        except (OSError, ValueError, TypeError, TimeoutError, asyncio.IncompleteReadError) as exc:
            self.journal.event('app', 'Conexão Wyoming: ' + str(exc), error=True)
            with suppress(OSError):
                writer.write(encode_event('error', {'text': str(exc), 'code': 'module_unavailable'}))
                await writer.drain()
        finally:
            for pump in pumps:
                pump.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            await close_writer(backend_writer)
            await close_writer(writer)
            self.clients.discard(task)
