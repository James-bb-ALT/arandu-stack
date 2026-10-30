"""FIXTURE_TECNICA: internal HTTP and model lifecycle without loading weights."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).parents[1]
for key in ('decision','llm'):
    sys.path.append(str(ROOT / f'arandu_stack/rootfs/modules/arandu_{key}/rootfs/src/src'))
from arandu_llm.service import create_app, retrieve
from arandu_llm.adapter import Llama
from arandu_llm.contracts import MODELS
from arandu_decision.adapter import Encoder
from arandu_decision.contracts import VERSION, digest
from aiohttp.test_utils import TestClient, TestServer


def catalog():
    return {'areas':[{'area_id':'sala','names':['Sala']}], 'entities':[
        {'entity_id':'fan.fixture','name':'Ventilador','aliases':[],'domain':'fan','area_id':'sala',
         'actions':['turn_on','turn_off'],'attributes':{'available':True}}]}


def proposal():
    return dict(intent='turn_on',target_entities=['fan.fixture'],target_area=None,confidence=None,
                requires_clarification=False,clarification_question=None,reason='FIXTURE_TECNICA')


class LlmHttp(unittest.IsolatedAsyncioTestCase):
    async def test_standalone_retrieval_without_decision_process(self):
        async def infer(body, catalog):
            self.assertTrue(body['decision']['ranking'])
            self.assertEqual(body['decision']['retrieval'],'lexical-full-catalog')
            return {'proposal':proposal()}
        with tempfile.TemporaryDirectory() as directory, patch('arandu_llm.service.Llama') as cls:
            cls.return_value.infer=AsyncMock(side_effect=infer)
            cls.return_value.unload=AsyncMock()
            client=TestClient(TestServer(create_app(Path(directory))))
            await client.start_server()
            try:
                response=await client.post('/infer',json={'action':'llm','text':'liga ventilador','catalog':catalog()},
                    headers={'Host':'127.0.0.1:11558','X-Arandu-Lab':'1'})
                self.assertEqual(response.status,200)
                result=await response.json()
                self.assertEqual(result['retrieval']['ranking'][0]['entity_id'],'fan.fixture')
                self.assertGreaterEqual(result['retrieval_ms'],0)
            finally:
                await client.close()


class Readiness(unittest.TestCase):
    def test_catalog_and_model_changes_invalidate_calibration_without_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'e5-small').mkdir()
            manifest={'revision':'fixture'}
            (root/'e5-small/arandu-model.json').write_text(json.dumps(manifest))
            calibration={'version':VERSION,'validated':True,'catalog':digest(catalog()),
                         'model':digest({'key':'e5-small','adapter':'embedding-v1','manifest':manifest})}
            (root/'e5-small-calibration.json').write_text(json.dumps(calibration))
            encoder=Encoder(root)
            self.assertTrue(encoder.readiness(catalog())['calibration_valid'])
            self.assertIsNone(encoder.model)
            changed=catalog()
            changed['entities'][0]['name']='Alterado'
            self.assertFalse(encoder.readiness(changed)['can_accept'])
            (root/'e5-small/arandu-model.json').write_text(json.dumps({'revision':'other'}))
            self.assertFalse(encoder.readiness(catalog())['calibration_valid'])


class LlmLifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_weights_reused_and_unload_stops_single_server(self):
        class Response:
            status=200
            class Content:
                async def iter_chunked(self,size):
                    raw=json.dumps({'choices':[{'message':{'content':json.dumps(proposal())}}]}).encode()
                    yield raw[:12]
                    yield raw[12:]
            content=Content()
            async def __aenter__(self): return self
            async def __aexit__(self,*args): pass
        class Session:
            async def __aenter__(self): return self
            async def __aexit__(self,*args): pass
            def get(self,*args,**kwargs): return Response()
            def post(self,*args,**kwargs): return Response()
        process=SimpleNamespace(returncode=None,pid=12345,kill=Mock(),wait=AsyncMock())
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'tucano').mkdir()
            (root/'tucano'/MODELS['tucano']['file']).touch()
            adapter=Llama(root,'FIXTURE_TECNICA')
            with patch('arandu_llm.adapter.asyncio.create_subprocess_exec',AsyncMock(return_value=process)) as spawn, patch('aiohttp.ClientSession',Session):
                body={'text':'liga ventilador','decision':retrieve('liga ventilador',catalog())}
                await adapter.infer(body,catalog())
                second=await adapter.infer(body,catalog())
                self.assertEqual(spawn.await_count,1)
                self.assertEqual(second['load_ms'],0)
                self.assertFalse(process.kill.called)
                await adapter.unload()
                process.kill.assert_called_once()
                self.assertIsNone(adapter.process)
