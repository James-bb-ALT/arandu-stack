"""Real Wyoming socket admission, without loading model weights."""
import asyncio
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest


@unittest.skipUnless(importlib.util.find_spec('wyoming'), 'Wyoming runtime required')
class Admission(unittest.IsolatedAsyncioTestCase):
    async def test_capture_limit_rejects_and_disconnect_releases_slot(self):
        source = Path(__file__).parents[1]/'arandu_stack/rootfs/modules/arandu_stt/rootfs/src/arandu_stt'
        sys.path.insert(0, str(source))
        from wyoming.event import async_read_event
        spec = importlib.util.spec_from_file_location('admission_stt_server', source/'server.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        engine = SimpleNamespace(clients=0, client_limit=1)
        tasks = set()

        async def connected(reader, writer):
            task = asyncio.current_task()
            tasks.add(task)
            try:
                await module.Handler(engine, None, None, 0, 'raw', reader, writer).run()
            finally:
                tasks.discard(task)

        server = await asyncio.start_server(connected, '127.0.0.1', 0)
        port = server.sockets[0].getsockname()[1]
        writers = []
        try:
            _, first = await asyncio.open_connection('127.0.0.1', port)
            writers.append(first)
            await asyncio.sleep(.02)
            reader, rejected = await asyncio.open_connection('127.0.0.1', port)
            writers.append(rejected)
            event = await asyncio.wait_for(async_read_event(reader), 1)
            self.assertEqual(event.type, 'error')
            self.assertEqual(event.data['code'], 'queue_saturated')
            self.assertEqual(engine.clients, 1)
            first.close()
            await first.wait_closed()
            for _ in range(50):
                if not engine.clients:
                    break
                await asyncio.sleep(.01)
            self.assertEqual(engine.clients, 0)
            _, replacement = await asyncio.open_connection('127.0.0.1', port)
            writers.append(replacement)
            await asyncio.sleep(.02)
            self.assertEqual(engine.clients, 1)
        finally:
            for writer in writers:
                writer.close()
                await writer.wait_closed()
            server.close()
            await server.wait_closed()
            for task in tuple(tasks):
                task.cancel()
            await asyncio.gather(*tuple(tasks), return_exceptions=True)
            sys.path.remove(str(source))
        self.assertEqual(engine.clients, 0)
