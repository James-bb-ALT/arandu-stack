"""Resource failure and lifecycle races; no real child processes or HA actions."""
import asyncio
import logging
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).parents[1]/'arandu_stack/app'))
from modules import ModuleManager
from test_control import control
from aiohttp.test_utils import TestClient, TestServer


class Resources(unittest.IsolatedAsyncioTestCase):
    async def test_recovery_has_three_attempts_and_respects_disable(self):
        with tempfile.TemporaryDirectory() as directory:
            manager=ModuleManager(Path(directory),local=True)
            manager.config['decision']['enabled']=True
            manager.start=AsyncMock(side_effect=OSError('fixture'))
            for _ in range(5):
                manager.recovery.setdefault('decision',{'attempts':0,'next':0})['next']=0
                await manager._recover('decision')
            self.assertEqual(manager.start.await_count,3)
            manager.recovery['decision']={'attempts':0,'next':0}
            manager.config['decision']['enabled']=False
            await manager._recover('decision')
            self.assertEqual(manager.start.await_count,3)

    async def test_zero_available_memory_is_degraded_without_killing(self):
        with tempfile.TemporaryDirectory() as directory:
            manager=ModuleManager(Path(directory),local=True)
            manager.processes['nlu']=SimpleNamespace(pid=123456,returncode=None)
            process=SimpleNamespace(memory_info=Mock(return_value=SimpleNamespace(rss=1048576)),
                cpu_times=Mock(return_value=(0.,0.)),children=Mock(return_value=[]))
            with patch('psutil.Process',return_value=process), patch('psutil.virtual_memory',return_value=SimpleNamespace(available=0)), \
                    patch('aiohttp.ClientSession',side_effect=OSError('fixture')), \
                    patch('modules.asyncio.sleep',new=AsyncMock(side_effect=asyncio.CancelledError)):
                with self.assertRaises(asyncio.CancelledError):
                    await manager._monitor()
            self.assertEqual(manager.health['nlu']['reason'],'memory_pressure')
            self.assertEqual(manager.health['nlu']['state'],'degraded')
            self.assertIsNone(manager.processes['nlu'].returncode)

    async def test_api_mutation_uses_the_recovery_commit_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            manager=ModuleManager(Path(directory),local=True)
            stopped=asyncio.Event()
            async def stop(key,persist=True):
                stopped.set()
                manager.config[key]['enabled']=False
            manager.stop=AsyncMock(side_effect=stop)
            with patch.object(control,'ModuleManager',return_value=manager):
                app=control.create_app(Path(directory),local=True)
            app.on_startup.clear();app.on_cleanup.clear()
            client=TestClient(TestServer(app))
            await client.start_server()
            try:
                state=await (await client.get('/api/state',headers={'Host':'127.0.0.1:8770'})).json()
                headers={'Host':'127.0.0.1:8770','X-Arandu-CSRF':state['csrf']}
                async with manager.lock:
                    request=asyncio.create_task(client.post('/api/modules/decision/stop',json={},headers=headers))
                    with self.assertRaises(TimeoutError):
                        await asyncio.wait_for(stopped.wait(),.05)
                response=await asyncio.wait_for(request,1)
                self.assertEqual(response.status,200)
                self.assertTrue(stopped.is_set())
            finally:
                await client.close()
                for handler in tuple(logging.getLogger().handlers):
                    filename=getattr(handler,'baseFilename',None)
                    if filename and Path(filename).is_relative_to(Path(directory)):
                        logging.getLogger().removeHandler(handler)
                        handler.close()
