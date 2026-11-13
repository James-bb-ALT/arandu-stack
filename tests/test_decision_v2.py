"""Approved requirement examples / FIXTURE_TECNICA, not statistical calibration gold."""
import copy
import tempfile
import unittest
import asyncio
import json
import time
import os
import sys
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

import test_semantic_confirmation as fixtures
from test_intelligence_services import catalog
from arandu_decision.adapter import Encoder
from arandu_decision.structured import policy, model_binding, lower_bound, calibrate, operational_scope
from custom_components.local_nlu.decision_context import fields, semantic_fingerprint, CALIBRATION
from custom_components.local_nlu.intelligence import accepted_decision, proposal_route


class Fields(unittest.TestCase):
    def test_unknown_named_room_cannot_fall_back_to_origin(self):
        data=catalog()
        data['areas']=[{'area_id':'sala','names':['Sala']}]
        result=fields('Liga o ventilador do quarto',data,{'origin_area':'sala'})
        self.assertEqual(result['refusal'],'unknown_explicit_area')
        data['entities'].append({**data['entities'][0],'entity_id':'light.fixture','domain':'light','name':'Luz do banheiro'})
        result=fields('Liga o ventilador do banheiro',data,{'origin_area':'sala'})
        self.assertEqual(result['refusal'],'unknown_explicit_area')

    def test_distributed_context_contract_is_identical(self):
        root=Path(__file__).resolve().parents[1]
        self.assertEqual((root/'custom_components/local_nlu/decision_context.py').read_bytes(),
                         (root/'arandu_stack/rootfs/modules/arandu_decision/rootfs/src/src/arandu_decision/decision_context.py').read_bytes())

    def test_catalog_tail_is_not_truncated(self):
        data=catalog()
        data['entities']=[{**data['entities'][0],'entity_id':f'fan.fixture_{i}','name':f'Device {i}'} for i in range(1024)]
        result=fields('liga Device 1023',data)
        self.assertEqual(result['device']['entity_id'],'fan.fixture_1023')
        self.assertEqual(result['device']['eligible_count'],1024)
    def test_explicit_constructions_are_independently_verified_by_ha(self):
        for text in ['Dá uma ligada no ventilador aqui', 'Coloca o ventilador pra funcionar',
                     'Quero o ventilador ligado', 'Deixa o ventilador ligado', 'liga ventilador']:
            with self.subTest(text=text), tempfile.TemporaryDirectory() as directory:
                engine = Encoder(Path(directory))
                with patch.object(engine, 'load', side_effect=AssertionError('no E5 for explicit fields')):
                    result = engine.infer(text, catalog(), 'e5-small', {'algorithm':'structured-v3'}, {'origin_area':'sala'})
                self.assertEqual(result['decision_status'], 'resolved', result)
                self.assertEqual(accepted_decision(result,catalog(),text,{'origin_area':'sala'})['intent'],'turn_on')
                forged = copy.deepcopy(result); forged['proposal']['intent'] = 'turn_off'
                with self.assertRaises(ValueError):accepted_decision(forged,catalog(),text,{'origin_area':'sala'})

    def test_advanced_restrictions_and_ambiguity_never_become_power_actions(self):
        data = catalog()
        for text in ['Deixa a sala mais clara','Diminui um pouco o ventilador',
                     'Coloca a temperatura em 23 graus','Fecha a cortina até a metade',
                     'Apaga as luzes da sala e da cozinha','Não liga o ventilador','Jamais desligue a luz',
                     'Liga tudo menos a TV','Se estiver quente liga o ar','liga aquilo']:
            result = fields(text,data,{'origin_area':'sala'})
            self.assertTrue(result['refusal'] or not result['device']['unambiguous'],(text,result))
        second = copy.deepcopy(data['entities'][0]); second['entity_id'] = 'fan.other'
        data['entities'].append(second)
        self.assertFalse(fields('liga ventilador',data)['device']['unambiguous'])

    def test_physical_switch_alias_never_invents_capability(self):
        data = catalog(); row = data['entities'][0]
        row.update(entity_id='switch.fixture',domain='switch',name='Relé',aliases=['Ventilador'])
        self.assertEqual(fields('liga ventilador',data)['device']['entity_id'],'switch.fixture')
        row['actions'] = ['query']
        self.assertIsNone(fields('liga ventilador',data)['device']['entity_id'])

    def test_semantic_binding_ignores_dynamic_values_but_captures_candidate_changes(self):
        data = catalog(); original = semantic_fingerprint(data)
        changed = copy.deepcopy(data)
        changed['generation'] = 'different'
        changed['entities'][0]['attributes'].update(available=False,current_temperature=27)
        self.assertEqual(original,semantic_fingerprint(changed))
        for key,value in [('aliases',['Brisa']),('actions',['turn_on']),('area_id',None),('name','Outro')]:
            altered = copy.deepcopy(data);altered['entities'][0][key] = value
            self.assertNotEqual(original,semantic_fingerprint(altered),key)

    def test_previous_area_requires_authorized_context_and_explicit_area_wins(self):
        data = catalog();data['areas'].append({'area_id':'quarto','names':['Quarto']})
        self.assertEqual(fields('liga ventilador de lá',data)['refusal'],'missing_previous_area')
        value = fields('liga ventilador da sala',data,{'origin_area':'quarto'})
        self.assertEqual(value['area'],{'area_id':'sala','source':'explicit','evidence':'explicit'})

    def test_spelling_entity_can_only_offer_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            result=Encoder(Path(directory)).infer('liga ventilado',catalog(),'e5-small',{'algorithm':'structured-v3'})
        self.assertEqual(result['decision_status'],'confirmation_required',result)
        with self.assertRaises(ValueError):
            accepted_decision({**result,'decision_status':'resolved','grounding':'deterministic-v2'},catalog(),'liga ventilado')


class Calibration(unittest.TestCase):
    def test_technical_fixture_records_metrics_without_enabling_production(self):
        def samples(prefix,count):
            return [{'text':f'FIXTURE_TECNICA {prefix} {i}','expected_intent':'turn_on',
                     'expected_entities':['fan.fixture'],'requires_clarification':False} for i in range(count)]
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);previous=root/'e5-small-calibration.json';previous.write_text('{"legacy":true}')
            engine=Encoder(root);engine.key='fixture'
            result={'ranking':[{'entity_id':'fan.fixture','intent':'turn_on','raw_score':.9}],
                    'margin':.2,'analysis':{'action':{'exact':None}}}
            with patch('arandu_decision.structured.infer',return_value=result):
                artifact=calibrate(engine,catalog(),'e5-small',samples('train',50),samples('validation',30),{'kind':'technical_fixture'})
            self.assertFalse(artifact['validated'])
            self.assertEqual(artifact['ece'],0)
            self.assertEqual(artifact['brier'],0)
            self.assertEqual(artifact['strata']['semantic_intent']['validation_bins'][4]['n'],30)
            self.assertEqual(previous.read_text(),'{"legacy":true}')
            from arandu_decision.structured import calibration_path
            self.assertTrue(calibration_path(root).is_file())

    def test_synthetic_or_previous_algorithm_artifact_never_accepts(self):
        data=catalog();rank=[{'entity_id':'fan.fixture','intent':'turn_on','raw_score':.95}]
        artifact={'version':CALIBRATION,'model':model_binding('weights'),'semantic_catalog':semantic_fingerprint(data),
                  'validated':True,'provenance':{'representative':False},
                  'bins':[{'n':100,'correct':100}]*5,'validation_bins':[{'n':100,'correct':100}]*5}
        artifact['strata']={'semantic_intent':{'bins':artifact['bins'],'validation_bins':artifact['validation_bins']}}
        artifact['operational_scope']=operational_scope(data)
        self.assertEqual(policy(rank,'weights',data,{},artifact)['decision_status'],'uncalibrated')
        artifact['provenance']['representative']=True
        self.assertEqual(policy(rank,'weights',data,{},artifact)['decision_status'],'accepted')
        unavailable=copy.deepcopy(data);unavailable['entities'][0]['attributes']['available']=False
        self.assertEqual(policy(rank,'weights',unavailable,{},artifact)['reason'],'operational_scope_changed')
        artifact['version']='arandu-joint-v2'
        self.assertEqual(policy(rank,'weights',data,{},artifact)['decision_status'],'uncalibrated')
        self.assertLess(lower_bound(10,10),.8)

    def test_normalized_duplicate_labels_do_not_count_as_independence(self):
        sample={'text':'Liga ventilador!','expected_intent':'turn_on','expected_entities':['fan.fixture'],'requires_clarification':False}
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                calibrate(Encoder(Path(directory)),catalog(),'e5-small',[sample]*50,[{**sample,'text':'liga ventilador'}]*30,{})


class Production(unittest.IsolatedAsyncioTestCase):
    say = fixtures.Confirmation.say

    async def asyncSetUp(self):
        await fixtures.Confirmation.asyncSetUp(self)
        self.options.update(decision_enabled=True,llm_enabled=True)
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.engine=Encoder(Path(self.directory.name))

    async def infer(self, session, endpoint, path, *, payload, **kwargs):
        self.requests.append(payload)
        self.assertEqual(payload['action'],'decision','A grounded result must avoid LLM')
        return self.engine.infer(payload['text'],payload['catalog'],'e5-small',{'algorithm':'structured-v3'},payload.get('context'))

    async def test_paraphrase_executes_through_existing_ha_boundary_once(self):
        result=await self.say('Coloca o ventilador pra funcionar')
        self.assertEqual(result.code,'success',result)
        self.assertEqual(len(self.hass.services.calls),1)
        await self.say('Coloca o ventilador pra funcionar')
        self.assertEqual(len(self.hass.services.calls),1)
        self.assertEqual([r['action'] for r in self.requests],['decision','decision'])

    async def test_ambiguity_escalates_to_llm_without_unsafe_execution(self):
        fixtures.add(self.hass,'other','fan.other','Ventilador',{'supported_features':49,'percentage':50},area='area_quarto')
        result=await self.say('liga ventilador')
        self.assertNotEqual(result.code,'success')
        self.assertEqual([r['action'] for r in self.requests],['decision', 'llm'])
        self.assertEqual(self.hass.services.calls,[])

    async def test_revoked_permission_during_inference_invalidates_result(self):
        original=self.infer
        async def changed(*args,**kwargs):
            result=await original(*args,**kwargs)
            self.hass.auth.user.is_admin=False
            self.hass.auth.user.permissions.allowed=set()
            return result
        with patch('custom_components.local_nlu.ai_lab.local_request',changed):
            result=await self.say('Coloca o ventilador pra funcionar')
        self.assertNotEqual(result.code,'success')
        self.assertEqual(self.hass.services.calls,[])

    async def test_timeout_never_executes_late_result(self):
        original=self.infer
        async def delayed(*args,**kwargs):
            await asyncio.sleep(.1)
            return await original(*args,**kwargs)
        self.options['llm_enabled']=False
        with patch('custom_components.local_nlu.ai_lab.local_request',delayed),patch('custom_components.local_nlu.intelligence.TIMEOUT',.01):
            await self.say('Coloca o ventilador pra funcionar')
            await asyncio.sleep(.12)
        self.assertEqual(self.hass.services.calls,[])


class Service(unittest.IsolatedAsyncioTestCase):
    async def test_real_endpoint_contract_and_bounded_busy_response(self):
        from aiohttp.test_utils import TestClient, TestServer
        from arandu_decision.service import create_app
        with tempfile.TemporaryDirectory() as directory:
            app=create_app(Path(directory),runtime_settings={'algorithm':'structured-v3'})
            client=TestClient(TestServer(app));await client.start_server()
            self.addAsyncCleanup(client.close)
            headers={'Host':'127.0.0.1:11557','X-Arandu-Lab':'1'}
            body={'action':'decision','text':'Coloca o ventilador pra funcionar','catalog':catalog()}
            response=await client.post('/infer',json=body,headers=headers)
            self.assertEqual(response.status,200)
            result=await response.json()
            self.assertEqual(result['algorithm'],'structured-v3')
            self.assertEqual(result['decision_status'],'resolved')
            self.assertGreaterEqual(result['queue_ms'],0)
            response=await client.post('/infer',json=body)
            self.assertEqual(response.status,403)
            def slow(*args,**kwargs):time.sleep(.1);return {'proposal':None,'decision_status':'rejected'}
            with patch('arandu_decision.adapter.Encoder.infer',side_effect=slow):
                pending=asyncio.create_task(client.post('/infer',json=body,headers=headers))
                await asyncio.sleep(.02)
                busy=await client.post('/infer',json=body,headers=headers)
                self.assertEqual(busy.status,429)
                await pending


class Migration(unittest.TestCase):
    def test_algorithm_persists_without_making_legacy_modules_json_unreadable(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'arandu_stack/app'))
        from modules import ModuleManager
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);manager=ModuleManager(root,local=True)
            manager.config['decision'].update(algorithm='baseline-v2',threads=3)
            manager.save()
            saved=json.loads((root/'modules.json').read_text())
            self.assertNotIn('algorithm',saved['modules']['decision'])
            restored=ModuleManager(root,local=True)
            self.assertEqual(restored.config['decision']['algorithm'],'baseline-v2')
            self.assertEqual(restored.config['decision']['threads'],3)
            (root/'decision-policy.json').write_text('broken')
            recovered=ModuleManager(root,local=True)
            self.assertEqual(recovered.config['decision']['algorithm'],'baseline-v2')
            self.assertIn('decision',recovered.failures)


@unittest.skipUnless(os.environ.get('ARANDU_DECISION_STACK_URL') and os.environ.get('ARANDU_NLU_ENDPOINT'), 'Opt-in packaged stack and real Rust')
class RealStack(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp import ClientSession
        from test_contextual import HttpClient
        from custom_components.local_nlu.contextual_runtime import ContextualRuntime
        self.session=ClientSession();self.addAsyncCleanup(self.session.close)
        self.hass=fixtures.hass_();self.hass.auth.user.id='user'
        fixtures.add(self.hass,'satellite','assist_satellite.fixture','Satélite',{},area='area_quarto')
        self.client=HttpClient(os.environ['ARANDU_NLU_ENDPOINT'])
        self.options={'stack_endpoint':os.environ['ARANDU_DECISION_STACK_URL'],'decision_enabled':True,'llm_enabled':True}
        self.runtime=ContextualRuntime(self.hass,self.client,lambda:self.options);self.addCleanup(self.runtime.close)
        helper=SimpleNamespace(async_get_clientsession=lambda _:self.session)
        self.modules=patch.dict(sys.modules,{'homeassistant.helpers.aiohttp_client':helper});self.modules.start();self.addCleanup(self.modules.stop)

    async def test_real_rust_gateway_decision_and_simulated_ha_execution(self):
        value=fixtures.input_(text='Coloca o ventilador pra funcionar',satellite_id='assist_satellite.fixture')
        result=await self.runtime.process(value)
        self.assertEqual(result.code,'success',result)
        self.assertEqual(len(self.hass.services.calls),1)
        self.assertNotIn('llm',self.runtime.last_intelligence)
        for text in ['Não liga o ventilador','Se estiver quente liga o ar','Liga tudo menos a TV']:
            value.text=text
            await self.runtime.process(value)
            self.assertEqual(len(self.hass.services.calls),1)

    async def test_native_lab_comparison_uses_same_catalog_and_never_executes(self):
        from custom_components.local_nlu.ai_lab import Lab
        entry=SimpleNamespace(entry_id='fixture',options=self.options,runtime_data=SimpleNamespace(_client=self.client))
        self.hass.config_entries=SimpleNamespace(async_entries=lambda _:[entry])
        core=SimpleNamespace(Context=lambda user_id:SimpleNamespace(user_id=user_id))
        with patch.dict(sys.modules,{'homeassistant.core':core}):
            lab=Lab(self.hass)
            result=await lab._handle(self.hass.auth.user,{'command':'decision_compare','test_area_id':'area_quarto',
                'examples':[{'text':'Coloca o ventilador pra funcionar','expected':{'intent':'turn_on','target_entities':['fan.ventilador']}}]},
                self.session,os.environ['ARANDU_DECISION_STACK_URL'])
        self.assertTrue(result['dry_run'])
        self.assertFalse(result['execution_allowed'])
        self.assertEqual(len(result['algorithm_comparison'][0]['comparison']['algorithm_comparison']),2)
        self.assertEqual(result['metrics']['structured-v3']['correct'],1)
        self.assertEqual(self.hass.services.calls,[])
        self.assertEqual(lab.proposals,{})
