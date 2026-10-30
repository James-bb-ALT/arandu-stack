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
from arandu_llm.adapter import Llama, LlmOutputError, parse_completion
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
    async def test_truncation_preserves_public_error_without_raw_output(self):
        with tempfile.TemporaryDirectory() as directory, patch('arandu_llm.service.Llama') as cls:
            cls.return_value.infer=AsyncMock(side_effect=LlmOutputError('llm_output_truncated','Limite de geração atingido; nenhuma ação autorizada.'))
            cls.return_value.unload=AsyncMock()
            client=TestClient(TestServer(create_app(Path(directory))))
            await client.start_server()
            try:
                response=await client.post('/infer',json={'action':'llm','text':'liga ventilador','catalog':catalog()},
                    headers={'Host':'127.0.0.1:11558','X-Arandu-Lab':'1'})
                self.assertEqual(response.status,422)
                result=await response.json()
                self.assertEqual(result['code'],'llm_output_truncated')
                self.assertEqual(result['phase'],'validation')
                self.assertNotIn('proposal',result)
            finally:
                await client.close()

    async def test_standalone_retrieval_without_decision_process(self):
        async def infer(body, catalog):
            self.assertTrue(body['decision']['ranking'])
            self.assertEqual(body['decision']['retrieval'],'lexical-full-catalog')
            self.assertEqual(body['decision']['analysis']['action']['text'],'liga')
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


class CompletionContract(unittest.TestCase):
    def completion(self, content, finish='stop'):
        return {'choices':[{'finish_reason':finish,'message':{'content':content}}]}

    def test_length_rejected_even_when_partial_json_happens_to_be_valid(self):
        for content in ['{\n "intent": "turn_on",\n ',json.dumps(proposal())]:
            with self.assertRaises(LlmOutputError) as raised:
                parse_completion(self.completion(content,'length'),catalog(),{'fan.fixture'},64)
            self.assertEqual(raised.exception.code,'llm_output_truncated')
            self.assertIn('64 tokens',str(raised.exception))

    def test_invalid_json_is_not_repaired_or_echoed(self):
        with self.assertRaises(LlmOutputError) as raised:
            parse_completion(self.completion('{"intent":"turn_on", FIXTURE_PRIVATE'),catalog(),{'fan.fixture'},192)
        self.assertEqual(raised.exception.code,'llm_invalid_json')
        self.assertNotIn('FIXTURE_PRIVATE',str(raised.exception))

    def test_complete_json_still_requires_authorized_targets(self):
        self.assertEqual(parse_completion(self.completion(json.dumps(proposal())),catalog(),{'fan.fixture'},192),proposal())
        with self.assertRaises(ValueError):
            parse_completion(self.completion(json.dumps(proposal())),catalog(),set(),192)

    def test_invalid_envelope_and_finish_reason_fail_closed(self):
        for value in [{}, {'choices':[]}, self.completion(json.dumps(proposal()),'content_filter')]:
            with self.assertRaises(LlmOutputError):
                parse_completion(value,catalog(),{'fan.fixture'},192)


class PublicDiagnostics(unittest.IsolatedAsyncioTestCase):
    async def test_integration_displays_safe_actionable_truncation_error(self):
        from custom_components.local_nlu.ai_lab import local_request, StageError
        class Response:
            status=502
            class Content:
                async def iter_chunked(self,size):
                    yield b'{"code":"llm_output_truncated","phase":"validation","message":"FIXTURE_PRIVATE_TOKEN"}'
            content=Content()
            async def __aenter__(self): return self
            async def __aexit__(self,*args): pass
        session=SimpleNamespace(request=lambda *args,**kwargs:Response())
        with patch('custom_components.local_nlu.client.LocalNluClient._async_pinned_origin',AsyncMock(return_value='http://127.0.0.1:8770')):
            with self.assertRaises(StageError) as raised:
                await local_request(session,'http://127.0.0.1:8770','/infer',payload={})
        self.assertEqual(raised.exception.code,'llm_output_truncated')
        self.assertEqual(raised.exception.phase,'validation')
        self.assertIn('192 ou 256',str(raised.exception))
        self.assertNotIn('FIXTURE_PRIVATE_TOKEN',str(raised.exception))


class LlmLifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_weights_reused_and_unload_stops_single_server(self):
        requests=[]
        class Response:
            status=200
            class Content:
                finish='stop'
                async def iter_chunked(self,size):
                    raw=json.dumps({'choices':[{'finish_reason':self.finish,'message':{'content':json.dumps(proposal())}}]}).encode()
                    yield raw[:12]
                    yield raw[12:]
            content=Content()
            async def __aenter__(self): return self
            async def __aexit__(self,*args): pass
        class Session:
            async def __aenter__(self): return self
            async def __aexit__(self,*args): pass
            def get(self,*args,**kwargs): return Response()
            def post(self,*args,**kwargs):
                requests.append(kwargs['json'])
                return Response()
        process=SimpleNamespace(returncode=None,pid=12345,kill=Mock(),wait=AsyncMock())
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'tucano').mkdir()
            (root/'tucano'/MODELS['tucano']['file']).touch()
            adapter=Llama(root,'FIXTURE_TECNICA',settings={'max_tokens':64})
            with patch('arandu_llm.adapter.asyncio.create_subprocess_exec',AsyncMock(return_value=process)) as spawn, patch('aiohttp.ClientSession',Session):
                body={'text':'liga ventilador','decision':retrieve('liga ventilador',catalog())}
                body['decision']['analysis']={'action':{'text':'liga'},'device':{'entity_id':'fan.fixture'},'area':{'area_id':'sala'}}
                await adapter.infer(body,catalog())
                self.assertEqual(json.loads(requests[0]['messages'][1]['content'])['decision_analysis'],body['decision']['analysis'])
                second=await adapter.infer(body,catalog())
                self.assertEqual(spawn.await_count,1)
                self.assertEqual(second['load_ms'],0)
                self.assertFalse(process.kill.called)
                Response.content.finish='length'
                with self.assertRaises(LlmOutputError):
                    await adapter.infer(body,catalog())
                self.assertEqual(len(requests),3)  # No implicit retry or budget override.
                self.assertFalse(process.kill.called)
                Response.content.finish='stop'
                await adapter.infer(body,catalog())
                self.assertEqual(spawn.await_count,1)
                self.assertTrue(all(r['max_tokens']==64 for r in requests))
                uncertain=await adapter.infer({**body,'text':'leva ventilador'},catalog())
                self.assertTrue(uncertain['confirmation_required'])
                self.assertTrue(uncertain['proposal']['requires_clarification'])
                self.assertIn('É isso?',uncertain['proposal']['clarification_question'])
                await adapter.unload()
                process.kill.assert_called_once()
                self.assertIsNone(adapter.process)
