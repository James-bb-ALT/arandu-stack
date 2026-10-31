"""FIXTURE_TECNICA: internal HTTP and model lifecycle without loading weights."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).parents[1]
for key in ('decision','llm'):
    sys.path.insert(0, str(ROOT / f'arandu_stack/rootfs/modules/arandu_{key}/rootfs/src/src'))
from arandu_llm.service import create_app, retrieve
from arandu_llm.adapter import Llama, LlmOutputError, parse_completion, prompt_context, SYSTEM_PROMPT
from arandu_llm.inference_slot import inference_slot as llm_slot
from arandu_decision.inference_slot import inference_slot as decision_slot
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
    def test_decision_reuses_model_and_configures_threads_once(self):
        from types import ModuleType
        torch = ModuleType('torch')
        torch.set_num_threads = Mock()
        torch.set_num_interop_threads = Mock()
        transformers = ModuleType('sentence_transformers')
        transformers.SentenceTransformer = Mock(return_value=SimpleNamespace(max_seq_length=None))
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, {
                'torch': torch, 'sentence_transformers': transformers}):
            root = Path(directory)
            (root/'e5-small').mkdir()
            (root/'e5-small/arandu-model.json').write_text('{"revision":"fixture"}')
            adapter = Encoder(root)
            adapter.load('e5-small')
            model = adapter.model
            adapter.load('e5-small')
            self.assertIs(adapter.model, model)
            self.assertEqual(adapter.load_ms, 0)
            transformers.SentenceTransformer.assert_called_once()
            torch.set_num_threads.assert_called_once_with(2)
            torch.set_num_interop_threads.assert_called_once_with(1)
            adapter.unload()
            adapter.load('e5-small')
            torch.set_num_interop_threads.assert_called_once_with(1)

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
            with patch('arandu_llm.adapter.asyncio.create_subprocess_exec',AsyncMock(return_value=process)) as spawn, patch('aiohttp.ClientSession',Session), patch('psutil.Process', side_effect=OSError):
                body={'text':'liga ventilador','decision':retrieve('liga ventilador',catalog())}
                body['decision']['analysis']={'action':{'text':'liga'},'device':{'entity_id':'fan.fixture'},'area':{'area_id':'sala'}}
                await adapter.infer(body,catalog())
                self.assertEqual(json.loads(requests[0]['messages'][1]['content'])['decision_analysis'],body['decision']['analysis'])
                second=await adapter.infer(body,catalog())
                self.assertEqual(spawn.await_count,1)
                args = spawn.call_args.args
                for flag, value in [('-t', '2'), ('-tb', '2'), ('--poll', '0'), ('--poll-batch', '0')]:
                    self.assertEqual(args[args.index(flag) + 1], value)
                self.assertNotIn('--cpu-mask', args)
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


class CompactPrompt(unittest.TestCase):
    def test_only_candidate_evidence_reaches_model(self):
        value = catalog()
        value['entities'].append({**value['entities'][0], 'entity_id': 'fan.other', 'name': 'Outro'})
        analysis = {'action': {'text': 'liga', 'exact': 'turn_on', 'ranking': [
            {'intent': 'turn_on', 'semantic_similarity': .987654321}]},
            'device': {'entity_id': 'fan.fixture', 'name': 'Ventilador', 'unambiguous': True,
                       'candidates': [{'entity_id': 'fan.fixture', 'name': 'Ventilador'}]},
            'area': {'area_id': 'sala', 'source': 'origin'}, 'note': 'FIXTURE_DEBUG' * 100}
        body = {'text': 'liga ventilador', 'nlu': {'debug': 'FIXTURE_DEBUG' * 100},
                'context': {'origin_area': 'sala', 'debug': 'FIXTURE_DEBUG'},
                'decision': {'ranking': [{'entity_id': 'fan.fixture', 'intent': 'turn_on', 'raw_score': .987654321}],
                             'analysis': analysis, 'reason': 'FIXTURE_DEBUG', 'decision_status': 'uncalibrated'}}
        compact, ids = prompt_context(body, value)
        self.assertEqual(ids, {'fan.fixture'})
        self.assertEqual([r['entity_id'] for r in compact['devices']], ['fan.fixture'])
        self.assertEqual(compact['devices'][0]['actions'], ['turn_on', 'turn_off'])
        self.assertEqual(compact['areas'], value['areas'])
        self.assertEqual(compact['context'], {'origin_area': 'sala'})
        self.assertEqual(compact['decision_analysis']['action']['ranking'][0]['semantic_similarity'], .988)
        self.assertNotIn('FIXTURE_DEBUG', json.dumps(compact))
        self.assertNotIn('candidates', compact['decision_analysis']['device'])
        self.assertEqual(body['decision']['analysis'], analysis)  # No mutation of diagnostics.
        self.assertLess(len(SYSTEM_PROMPT), 850)


class InferenceSerialization(unittest.IsolatedAsyncioTestCase):
    async def test_services_serialize_even_for_independent_http_requests(self):
        from arandu_decision.service import create_app as decision_app
        entered, release = asyncio.Event(), asyncio.Event()
        async def llm_infer(*args, **kwargs):
            entered.set()
            await release.wait()
            return {'proposal': proposal()}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'ARANDU_INFERENCE_LOCK': str(Path(directory)/'slot')}), \
                patch('arandu_llm.service.Llama') as llama, patch('arandu_decision.service.Encoder') as encoder:
            llama.return_value.infer = AsyncMock(side_effect=llm_infer)
            llama.return_value.unload = AsyncMock()
            encoder.return_value.infer.return_value = {'decision_status': 'uncalibrated'}
            clients = [TestClient(TestServer(create_app(Path(directory)))),
                       TestClient(TestServer(decision_app(Path(directory))))]
            for client in clients:
                await client.start_server()
            try:
                first = asyncio.create_task(clients[0].post('/infer', json={'text': 'liga ventilador', 'catalog': catalog()},
                    headers={'Host': '127.0.0.1:11558', 'X-Arandu-Lab': '1'}))
                await asyncio.wait_for(entered.wait(), 2)
                second = asyncio.create_task(clients[1].post('/infer', json={'text': 'liga ventilador', 'catalog': catalog()},
                    headers={'Host': '127.0.0.1:11557', 'X-Arandu-Lab': '1'}))
                await asyncio.sleep(.1)
                encoder.return_value.infer.assert_not_called()
                release.set()
                responses = await asyncio.wait_for(asyncio.gather(first, second), 2)
                self.assertEqual([r.status for r in responses], [200, 200])
                encoder.return_value.infer.assert_called_once()
            finally:
                release.set()
                for client in clients:
                    await client.close()

    async def test_lock_crosses_process_boundary_and_cancelled_waiter_does_not_release_owner(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'ARANDU_INFERENCE_LOCK': str(Path(directory)/'slot')}):
            code = ('import asyncio,sys; sys.path.insert(0,sys.argv[1]); '
                    'from arandu_llm.inference_slot import inference_slot\n'
                    'async def main():\n'
                    ' async with inference_slot():\n'
                    '  print("acquired",flush=True)\n'
                    'asyncio.run(main())\n')
            async def wait_for_slot():
                async with llm_slot():
                    self.fail('Waiter entered while Decision owns slot')
            async with decision_slot():
                waiter = asyncio.create_task(wait_for_slot())
                await asyncio.sleep(.06)
                waiter.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await waiter
                process = await asyncio.create_subprocess_exec(sys.executable, '-c', code,
                    str(ROOT/'arandu_stack/rootfs/modules/arandu_llm/rootfs/src/src'), stdout=asyncio.subprocess.PIPE)
                line = asyncio.create_task(process.stdout.readline())
                await asyncio.sleep(.15)
                self.assertFalse(line.done())
            try:
                self.assertEqual(await asyncio.wait_for(line, 5), b'acquired\n' if os.name != 'nt' else b'acquired\r\n')
                self.assertEqual(await asyncio.wait_for(process.wait(), 5), 0)
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
