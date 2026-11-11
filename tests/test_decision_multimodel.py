"""Technical fixtures. No weight downloads or physical GPU claims in unit tests."""
import asyncio
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import os
from types import SimpleNamespace
from unittest.mock import patch,Mock

import test_intelligence_services as fixtures
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'arandu_stack/app'))
from arandu_decision.contracts import MODELS,selection,settings
from arandu_decision.installation import install,verify,signature
from arandu_decision.backends import gpu_probe,BackendUnavailable,OpenVINOGPUBackend
from arandu_decision.adapter import Encoder
from arandu_decision.structured import calibration_path


def tiny_registry():
    return {key:{**info,'dimension':2,'files':{'weights.bin':{'size':4,'sha256':hashlib.sha256(b'good').hexdigest()}},'disk_bytes':4}
        for key,info in MODELS.items()}


def weights(root,registry):
    for key in registry:
        (root/key).mkdir();(root/key/'weights.bin').write_bytes(b'good')


class FakeBackend:
    def __init__(self):self.inputs=[];self.metrics={'tokenization_ms':1,'embedding_ms':2}
    def encode(self,texts,**kwargs):
        import numpy as np
        self.inputs.extend(texts);return np.array([[1.,0.] for _ in texts])
    def close(self):pass


class Registry(unittest.TestCase):
    def test_partial_labels_never_claim_full_plan_or_unlabelled_area_accuracy(self):
        from custom_components.local_nlu.ai_lab_core import decision_matrix
        rows=[{'combination':'e5-small:cpu','decision_status':'resolved','case':0,'wall_ms':1,
            'embedding_used':False,'proposal':{'intent':'turn_on','target_entities':['fan.fixture'],'requires_clarification':False}}]
        metrics=decision_matrix(rows,[{'expected':{'intent':'turn_on'}}],fixtures.catalog())['e5-small:cpu']
        self.assertEqual(metrics['labelled'],1);self.assertEqual(metrics['plan_labelled'],0);self.assertEqual(metrics['correct_plan'],0)
        self.assertEqual(metrics['intent_correct'],1);self.assertEqual(metrics['area_labelled'],0)
        self.assertEqual(metrics['embedding_requests'],0);self.assertEqual(metrics['deterministic_requests'],1)

    def test_pinned_registry_license_dimension_pooling_and_no_paths(self):
        self.assertEqual(set(MODELS),{'e5-small','minilm-multilingual','e5-base'})
        for key,info in MODELS.items():
            self.assertRegex(info['revision'],r'^[0-9a-f]{40}$')
            self.assertIn(info['license'],{'mit','apache-2.0'})
            self.assertEqual(info['dimension'],768 if key=='e5-base' else 384)
            self.assertEqual(info['pooling'],'attention-mask-mean')
            self.assertEqual(info['disk_bytes'],sum(f['size'] for f in info['files'].values()))
            for name,meta in info['files'].items():
                self.assertNotIn('..',Path(name).parts);self.assertFalse(Path(name).is_absolute())
                self.assertRegex(meta['sha256'],r'^[0-9a-f]{64}$')
        for bad in ['../model','e5-small/extra','unknown',None]:
            with self.assertRaises(ValueError):selection(bad)
        with self.assertRaises(ValueError):selection('e5-small','auto')
        self.assertEqual(settings({'threshold':.5,'min_margin':.001}),(.8,.08))

    def test_install_stages_verifies_pinned_bytes_and_preserves_old_on_error(self):
        registry=tiny_registry()
        with tempfile.TemporaryDirectory() as tmp,patch.dict(MODELS,registry):
            root=Path(tmp);dest=root/'e5-small';dest.mkdir();(dest/'weights.bin').write_bytes(b'old!')
            with patch('urllib.request.urlopen',return_value=io.BytesIO(b'evil')):
                with self.assertRaises(ValueError):install(root)
            self.assertEqual((dest/'weights.bin').read_bytes(),b'old!')
            with patch('urllib.request.urlopen',return_value=io.BytesIO(b'good')) as opened:
                self.assertTrue(install(root)['verified']);self.assertIn(MODELS['e5-small']['revision'],opened.call_args.args[0])
            verify(dest,'e5-small');self.assertFalse(list(root.glob('*staging*')))
            self.assertEqual(json.loads((dest/'arandu-model.json').read_text())['version'],2)

    def test_fast_path_and_safety_do_not_probe_or_load_gpu(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine=Encoder(Path(tmp))
            for model in MODELS:
                with patch.object(engine,'load',side_effect=AssertionError('no model for fast path')):
                    result=engine.infer('liga ventilador',fixtures.catalog(),model,{'algorithm':'structured-v3','backend':'gpu_intel'})
                    self.assertEqual(result['decision_status'],'resolved');self.assertFalse(result['embedding_used'])
                    result=engine.infer('Não liga ventilador',fixtures.catalog(),model,{'algorithm':'structured-v3','backend':'gpu_intel'})
                    self.assertEqual(result['decision_status'],'rejected');self.assertIsNone(result['proposal'])


class Runtime(unittest.TestCase):
    def test_prefixes_dynamic_dimensions_cache_isolation_and_failed_switch_rollback(self):
        registry=tiny_registry();torch=SimpleNamespace(set_num_threads=Mock(),set_num_interop_threads=Mock())
        with tempfile.TemporaryDirectory() as tmp,patch.dict(MODELS,registry),patch.dict(sys.modules,{'torch':torch}):
            root=Path(tmp);weights(root,registry);engine=Encoder(root);created=[]
            def create(root,key,backend,threads):
                if backend=='gpu_intel':raise BackendUnavailable('fixture GPU unavailable')
                result=FakeBackend();created.append(result);return result
            with patch('arandu_decision.backends.create_backend',create):
                first=engine.load('e5-small');engine.encode(['ventilador'],query=True)
                self.assertIn('query: ventilador',engine.model.inputs)
                engine.cache['fixture']=1;engine.load('minilm-multilingual')
                self.assertEqual(engine.cache,{});engine.encode(['ventilador'],query=True)
                self.assertIn('ventilador',engine.model.inputs);self.assertNotIn('query: ventilador',engine.model.inputs)
                with self.assertRaises(BackendUnavailable):engine.load('e5-base','gpu_intel')
                self.assertEqual(engine.active_model_key,'minilm-multilingual');self.assertEqual(engine.active_backend,'cpu')
                self.assertNotEqual(first,engine.key)
                engine.model.encode=lambda texts,**kw: __import__('numpy').zeros((len(texts),3))
                with self.assertRaises(ValueError):engine.encode(['bad dimension'])

    def test_calibration_isolated_by_model_backend_runtime_and_old_artifacts_rejected(self):
        root=Path('fixture')
        paths={calibration_path(root,key,backend) for key in MODELS for backend in ['cpu','gpu_intel']}
        self.assertEqual(len(paths),6)
        self.assertNotIn(root/'e5-small-structured-v3-calibration.json',paths)
        old=signature('e5-small','cpu')
        with patch('arandu_decision.backends.version',return_value='different'):
            self.assertNotEqual(signature('e5-small','cpu'),old)

    def test_gpu_unavailable_never_falls_back_to_cpu(self):
        with patch('arandu_decision.backends.version',return_value='unavailable'):
            result=gpu_probe();self.assertFalse(result['available']);self.assertFalse(result['validated'])
            self.assertIn('sem fallback',result['reason'])

    def test_gpu_requires_compile_gpu_and_actual_execution_devices(self):
        import numpy as np
        class Compiled:
            def get_property(self,key):return ['GPU.0']
            def output(self,index):return 'hidden'
            def __call__(self,data):return {'hidden':np.array([[[2.,0.],[0.,4.],[100.,100.]]])}
        compiled=Compiled();core=SimpleNamespace(compile_model=Mock(return_value=compiled))
        tokenizer=lambda *a,**kw:{'input_ids':np.array([[1,2,0]]),'attention_mask':np.array([[1,1,0]])}
        transformer=SimpleNamespace(AutoTokenizer=SimpleNamespace(from_pretrained=lambda *a,**kw:tokenizer))
        with patch.dict(sys.modules,{'openvino':SimpleNamespace(Core=lambda:core),'transformers':transformer}),patch('arandu_decision.backends.gpu_probe',return_value={'available':True}),patch('arandu_decision.backends.verify_ir',return_value=Path('fixture')),patch.dict(MODELS,tiny_registry()):
            backend=OpenVINOGPUBackend(Path('fixture'),'e5-small',2)
            self.assertEqual(core.compile_model.call_args.args[1],'GPU')
            actual=backend.encode(['test']);np.testing.assert_allclose(actual,[[1/np.sqrt(5),2/np.sqrt(5)]],rtol=1e-6)
            compiled.get_property=lambda key:['CPU']
            with self.assertRaises(BackendUnavailable):OpenVINOGPUBackend(Path('fixture'),'e5-small',2)


class Service(unittest.IsolatedAsyncioTestCase):
    async def test_status_and_gpu_failure_are_truthful_with_protected_transport(self):
        from aiohttp.test_utils import TestClient,TestServer
        from arandu_decision.service import create_app
        with tempfile.TemporaryDirectory() as tmp:
            client=TestClient(TestServer(create_app(Path(tmp))));await client.start_server();self.addAsyncCleanup(client.close)
            self.assertEqual((await client.get('/status')).status,403)
            headers={'Host':'127.0.0.1:11557','X-Arandu-Lab':'1'}
            result=await (await client.get('/status',headers=headers)).json()
            self.assertEqual({r['id'] for r in result['models']},set(MODELS));self.assertIsNone(result['active_model'])
            with patch('arandu_decision.service.gpu_probe',return_value={'available':False,'reason':'fixture GPU unavailable'}):
                result=await (await client.post('/infer',headers=headers,json={'action':'compare','text':'liga ventilador',
                    'catalog':fixtures.catalog(),'combinations':[{'model':'e5-small','backend':'gpu_intel'}]})).json()
            self.assertTrue(result['dry_run']);self.assertIsNone(result['proposal'])
            self.assertEqual(result['comparisons'][0]['decision_status'],'unsupported')

    async def test_decision_control_requires_csrf_confirmation_and_internal_transport(self):
        from aiohttp import web
        from aiohttp.test_utils import TestClient,TestServer,make_mocked_request
        from test_control import control
        from unittest.mock import AsyncMock
        with tempfile.TemporaryDirectory() as tmp,patch.object(control.LogStore,'configure_logging'),patch('modules.ModuleManager.start',new=AsyncMock()) as start:
            app=control.create_app(Path(tmp),local=True);app.on_startup.clear();app.on_cleanup.clear()
            client=TestClient(TestServer(app),headers={'Host':'127.0.0.1:8770'});await client.start_server();self.addAsyncCleanup(client.close)
            response=await client.post('/api/decision/apply',json={'model':'e5-base','backend':'cpu','confirm':True});self.assertEqual(response.status,403)
            self.assertEqual((await client.get('/api/decision/apply')).status,405)
            state=await (await client.get('/api/state')).json();headers={'X-Arandu-CSRF':state['csrf']}
            response=await client.post('/api/decision/apply',json={'model':'e5-base','backend':'cpu'},headers=headers);self.assertEqual(response.status,422)
            response=await client.post('/api/decision/install',json={'model':'../../escape'},headers=headers);self.assertEqual(response.status,422);start.assert_not_awaited()
            native=control.create_app(Path(tmp),local=False)
            async def handler(_):return web.json_response({'accepted':True})
            for peer,h,allowed in [('172.30.32.1',{'X-Arandu-Lab':'1'},True),('172.30.32.1',{},False),
                ('172.30.32.1',{'X-Arandu-Lab':'1','Origin':'https://attacker.invalid'},False),('203.0.113.1',{'X-Arandu-Lab':'1'},False)]:
                transport=SimpleNamespace(get_extra_info=lambda key,default=None,p=peer:(p,1234) if key=='peername' else default)
                request=make_mocked_request('POST','/api/decision/apply',headers=h,transport=transport,app=native)
                if allowed:self.assertEqual((await native.middlewares[0](request,handler)).status,200)
                else:
                    with self.assertRaises(web.HTTPForbidden):await native.middlewares[0](request,handler)


class Migration(unittest.IsolatedAsyncioTestCase):
    async def test_selection_roundtrip_legacy_compatibility_and_corrupt_policy_recovery(self):
        from modules import ModuleManager
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);manager=ModuleManager(root,local=True)
            await manager.configure('decision',{'model':'e5-base','backend':'gpu_intel'})
            saved=json.loads((root/'modules.json').read_text())
            self.assertNotIn('model',saved['modules']['decision']);self.assertNotIn('backend',saved['modules']['decision'])
            self.assertEqual(ModuleManager(root,local=True).config['decision']['model'],'e5-base')
            (root/'decision-model-policy.json').write_text('broken')
            recovered=ModuleManager(root,local=True);self.assertEqual(recovered.config['decision']['model'],'e5-small')
            self.assertEqual(recovered.config['decision']['backend'],'cpu');self.assertIn('decision',recovered.failures)


@unittest.skipUnless(os.environ.get('ARANDU_DECISION_STACK_URL') and os.environ.get('ARANDU_NLU_ENDPOINT'),'Opt-in isolated real models and Rust')
class RealStack(unittest.IsolatedAsyncioTestCase):
    from test_decision_v2 import RealStack as _Setup
    asyncSetUp=_Setup.asyncSetUp

    async def test_native_six_combination_matrix_and_test_use_actual_selected_models(self):
        from custom_components.local_nlu.ai_lab import Lab
        entry=SimpleNamespace(entry_id='fixture',options=self.options,runtime_data=SimpleNamespace(_client=self.client))
        self.hass.config_entries=SimpleNamespace(async_entries=lambda _:[entry])
        core=SimpleNamespace(Context=lambda user_id:SimpleNamespace(user_id=user_id))
        with patch.dict(sys.modules,{'homeassistant.core':core}):
            lab=Lab(self.hass)
            result=await lab._handle(self.hass.auth.user,{'command':'decision_matrix','test_area_id':'area_quarto',
                'combinations':[{'model':m,'backend':b} for m in MODELS for b in ['cpu','gpu_intel']],
                'repetitions':2,'warmup':1,'examples':[
                    {'text':'Coloca o ventilador pra funcionar','expected':{'intent':'turn_on','target_entities':['fan.ventilador'],'target_area':'area_quarto','requires_clarification':False}},
                    {'text':'Não liga o ventilador','expected':{'requires_clarification':True}}]},
                self.session,os.environ['ARANDU_DECISION_STACK_URL'])
            self.assertTrue(result['dry_run']);self.assertFalse(result['execution_allowed']);self.assertEqual(len(result['metrics']),6)
            for model in MODELS:
                self.assertEqual(result['metrics'][model+':cpu']['correct_plan'],4,result)
                gpu=result['metrics'][model+':gpu_intel']
                self.assertIn(gpu['status'],{'unsupported','pass'})
                if gpu['status']=='pass':self.assertEqual(gpu['correct_plan'],4)
            self.assertEqual(lab.proposals,{});self.assertEqual(self.hass.services.calls,[])
            result=await lab._handle(self.hass.auth.user,{'command':'run','mode':'decision','model':'minilm-multilingual',
                'backend':'cpu','text':'Coloca o ventilador pra funcionar','test_area_id':'area_quarto'},self.session,os.environ['ARANDU_DECISION_STACK_URL'])
            self.assertEqual(result['stages']['decision']['output']['model'],'minilm-multilingual')
            self.assertEqual(self.hass.services.calls,[])
            result=await lab._handle(self.hass.auth.user,{'command':'run','mode':'decision','model':'e5-base',
                'backend':'cpu','text':'leva o ventilador','test_area_id':'area_quarto'},self.session,os.environ['ARANDU_DECISION_STACK_URL'])
            actual=result['stages']['decision']['output']
            self.assertEqual(actual['model'],'e5-base');self.assertTrue(actual['embedding_used'])
            self.assertEqual(actual['backend_used'],'cpu');self.assertEqual(actual['dimension'],768)
            self.assertIsNone(actual['confidence']);self.assertEqual(self.hass.services.calls,[])
