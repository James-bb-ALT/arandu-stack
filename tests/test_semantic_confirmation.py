"""FIXTURE_TECNICA: calibrated Decision automation and LLM confirmation boundaries."""
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

ROOT=Path(__file__).parents[1]
sys.path.append(str(ROOT/'arandu_stack/rootfs/modules/arandu_nlu/rootfs/src/tests/mlp'))
sys.path.append(str(ROOT/'arandu_stack/rootfs/modules/arandu_decision/rootfs/src/src'))
from test_contextual import Client, hass_, input_, add, plan, operation
from custom_components.local_nlu.contextual_runtime import ContextualRuntime
from custom_components.local_nlu.ai_lab_core import digest
from custom_components.local_nlu.semantic_context import decomposition
from arandu_decision.adapter import Encoder


class Confirmation(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass=hass_()
        add(self.hass,'satellite','assist_satellite.fixture','Satélite',{},area='area_quarto')
        self.options={'stack_endpoint':'http://127.0.0.1:8770','llm_enabled':True}
        self.client=Client({'version':4,'status':'no_match'})
        self.runtime=ContextualRuntime(self.hass,self.client,lambda:self.options)
        self.addCleanup(self.runtime.close)
        self.requests=[]
        helper=SimpleNamespace(async_get_clientsession=lambda _:None)
        modules=patch.dict(sys.modules,{'homeassistant.helpers.aiohttp_client':helper})
        modules.start()
        self.addCleanup(modules.stop)
        mock=patch('custom_components.local_nlu.ai_lab.local_request',self.infer)
        mock.start()
        self.addCleanup(mock.stop)

    async def infer(self,session,endpoint,path,*,payload,**kwargs):
        self.requests.append(payload)
        p={'intent':'turn_on','target_entities':['fan.ventilador'],'target_area':None,'confidence':None,
           'requires_clarification':False,'clarification_question':None,'reason':'FIXTURE_TECNICA'}
        if payload['action']=='decision':
            return {'decision_status':'uncalibrated','proposal':None,'reason':'joint_calibration_missing',
                    'ranking':[{'entity_id':'fan.ventilador','intent':'turn_on','raw_score':.7}],
                    'analysis':decomposition(payload['text'],payload['catalog'],payload['context'])}
        return {'proposal':p,'retrieval':{'ranking':[{'entity_id':'fan.ventilador','intent':'turn_on'}]}}

    async def say(self,text='leva o ventilador'):
        return await self.runtime.process(input_(text=text,satellite_id='assist_satellite.fixture'))

    async def test_yes_executes_exact_hypothesis_once_without_inference_again(self):
        result=await self.say()
        self.assertEqual(result.code,'confirmation_required')
        self.assertIn('ligar',result.response_text)
        self.assertIn('É isso?',result.response_text)
        self.assertEqual(self.hass.services.calls,[])
        self.assertEqual((await self.say('sim')).code,'success')
        self.assertEqual(len(self.hass.services.calls),1)
        self.assertEqual(len(self.requests),1)
        self.assertNotEqual((await self.say('sim')).code,'success')
        self.assertEqual(len(self.hass.services.calls),1)

    async def test_no_cancels_and_cannot_be_confirmed_later(self):
        await self.say()
        self.assertEqual((await self.say('não')).code,'cancelled')
        self.assertNotEqual((await self.say('sim')).code,'success')
        self.assertEqual(self.hass.services.calls,[])

    async def test_corrected_command_replaces_hypothesis(self):
        await self.say()
        self.client.response=plan(operation('turn_off',['reg_fan']))
        self.assertEqual((await self.say('desliga o ventilador')).code,'success')
        self.assertEqual(len(self.hass.services.calls),1)
        self.assertNotEqual((await self.say('sim')).code,'success')
        self.assertEqual(len(self.hass.services.calls),1)

    async def test_decision_parts_reach_llm_without_uncalibrated_execution(self):
        self.options['decision_enabled']=True
        self.assertEqual((await self.say('acnde o ventilador')).code,'confirmation_required')
        self.assertEqual([r['action'] for r in self.requests],['decision','llm'])
        data=self.requests[1]['decision']['analysis']
        self.assertEqual(data['action']['text'],'acnde')
        self.assertEqual(data['device']['entity_id'],'fan.ventilador')
        self.assertEqual(data['area']['area_id'],'area_quarto')
        self.assertEqual(self.hass.services.calls,[])

    async def test_negation_does_not_become_a_confirmable_guess(self):
        self.assertNotEqual((await self.say('não leva o ventilador')).code,'confirmation_required')
        self.assertEqual(self.hass.services.calls,[])

    async def test_permission_revocation_between_question_and_yes(self):
        await self.say()
        self.hass.auth.user.is_admin=False
        self.hass.auth.user.permissions.allowed={('fan.ventilador','read')}
        self.assertNotEqual((await self.say('sim')).code,'success')
        self.assertEqual(self.hass.services.calls,[])

    async def test_yes_in_another_conversation_cannot_consume_confirmation(self):
        await self.say()
        other=input_(text='sim',conversation_id='other',satellite_id='assist_satellite.fixture')
        self.assertNotEqual((await self.runtime.process(other)).code,'success')
        self.assertEqual(self.hass.services.calls,[])
        self.assertEqual((await self.say('sim')).code,'success')
        self.assertEqual(len(self.hass.services.calls),1)

    async def test_origin_source_change_is_stale_even_with_same_default_area(self):
        self.options['default_area']='area_quarto'
        await self.say()
        self.hass.entity_registry.entities.pop('assist_satellite.fixture')
        self.assertNotEqual((await self.say('sim')).code,'success')
        self.assertEqual(self.hass.services.calls,[])

    async def test_calibrated_decision_executes_inferred_action_without_yes(self):
        self.options.update(decision_enabled=True,llm_enabled=False)
        async def calibrated(session,endpoint,path,*,payload,**kwargs):
            self.requests.append(payload)
            return {'decision_status':'accepted','confidence':.9,'margin':.2,'catalog_fingerprint':digest(payload['catalog']),
                    'proposal':{'intent':'turn_on','target_entities':['fan.ventilador'],'target_area':None,'confidence':.9,
                                'requires_clarification':False,'clarification_question':None,'reason':'joint_calibrated'},
                    'ranking':[{'entity_id':'fan.ventilador','intent':'turn_on','raw_score':.8}]}
        with patch('custom_components.local_nlu.ai_lab.local_request',calibrated):
            self.assertEqual((await self.say()).code,'success')
            self.assertEqual(len(self.hass.services.calls),1)
            self.assertEqual([r['action'] for r in self.requests],['decision'])
            self.assertNotEqual((await self.say('sim')).code,'success')
        self.assertEqual(len(self.hass.services.calls),1)

    async def test_stale_confirmation_never_executes(self):
        changes=[lambda:self.options.update(llm_enabled=False),
                 lambda:self.hass.exposed.remove('fan.ventilador'),
                 lambda:setattr(self.hass.entity_registry.entities['assist_satellite.fixture'],'area_id','area_sala'),
                 lambda:self.hass.states._states['fan.ventilador'].attributes.update(friendly_name='Alterado'),
                 lambda:setattr(next(iter(self.runtime.sessions.values())),'expires',time.monotonic()-1)]
        for change in changes:
            with self.subTest(change=change):
                self.runtime.sessions.clear()
                self.runtime.catalog.invalidate()
                self.options['llm_enabled']=True
                self.hass.exposed.add('fan.ventilador')
                self.hass.states._states['fan.ventilador'].attributes['friendly_name']='Ventilador'
                self.hass.entity_registry.entities['assist_satellite.fixture'].area_id='area_quarto'
                self.assertEqual((await self.say()).code,'confirmation_required')
                change()
                self.assertNotEqual((await self.say('sim')).code,'success')
                self.assertEqual(self.hass.services.calls,[])


    async def test_automatic_decision_boundary_and_rejections(self):
        self.options.update(decision_enabled=True,llm_enabled=False)
        for confidence, margin, text, execute in [
                (.8,.2,'leva o ventilador',True),
                (.799,.2,'leva o ventilador',False),
                (.9,.01,'leva o ventilador',False),
                (.9,.2,'não leva o ventilador',False),
                (.9,.2,'desliga o ventilador',False)]:
            self.runtime.sessions.clear()
            self.hass.services.calls.clear()
            self.requests.clear()
            async def calibrated(session,endpoint,path,*,payload,**kwargs):
                self.requests.append(payload)
                self.assertEqual(payload['config']['threshold'],.8)
                return {'decision_status':'accepted','confidence':confidence,'margin':margin,
                    'catalog_fingerprint':digest(payload['catalog']),
                    'proposal':{'intent':'turn_on','target_entities':['fan.ventilador'],'target_area':None,
                        'confidence':confidence,'requires_clarification':False,'clarification_question':None,'reason':'joint_calibrated'},
                    'ranking':[{'entity_id':'fan.ventilador','intent':'turn_on','raw_score':.99}]}
            with self.subTest(confidence=confidence,margin=margin,text=text), patch('custom_components.local_nlu.ai_lab.local_request',calibrated):
                result=await self.say(text)
                self.assertEqual(result.code=='success',execute)
                self.assertEqual(len(self.hass.services.calls),int(execute))
                self.assertEqual([r['action'] for r in self.requests],['decision'])
                if execute:
                    self.assertEqual(self.hass.services.calls[0]['service'],'turn_on')
                    self.assertTrue(self.runtime.last_intelligence['decision']['auto_corrected'])


class DecisionParts(unittest.TestCase):
    def test_calibrated_eighty_percent_is_inclusive_but_cosine_is_not_confidence(self):
        from arandu_decision.contracts import decision_policy, VERSION
        from test_intelligence_services import catalog
        value=catalog()
        ranking=[{'entity_id':'fan.fixture','intent':'turn_on','raw_score':.99}]
        cell={'n':10,'correct':8}
        calibration={'version':VERSION,'model':'fixture','catalog':digest(value),'validated':True,
            'bins':[dict(cell) for _ in range(5)],'validation_bins':[dict(cell) for _ in range(5)]}
        result=decision_policy(ranking,'fixture',value,{},calibration)
        self.assertEqual(result['decision_status'],'accepted')
        self.assertEqual(result['confidence'],.8)
        result=decision_policy(ranking,'fixture',value,{},None)
        self.assertEqual(result['decision_status'],'uncalibrated')
        self.assertIsNone(result['confidence'])
        self.assertIsNone(result['proposal'])

    def test_high_confidence_decision_does_not_add_verbal_confirmation(self):
        from test_semantic_clarification import catalog, proposed
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            encoder=Encoder(Path(directory))
            encoder.load=Mock(return_value='FIXTURE_TECNICA')
            encoder.model=SimpleNamespace(encode=lambda texts,**kwargs:[[1.,0.,0.] for _ in texts])
            proposal={**proposed(),'confidence':.9}
            encoder.finish=Mock(return_value={'decision_status':'accepted','proposal':proposal,'confidence':.9})
            with patch.dict(sys.modules,{'numpy':SimpleNamespace(dot=lambda a,b:sum(x*y for x,y in zip(a,b)))}):
                result=encoder.infer('leva o ventilador',catalog(),'e5-small',{},context={'origin_area':'office'})
            self.assertFalse(result.get('confirmation_required',False))
            self.assertFalse(result['proposal']['requires_clarification'])
            self.assertIsNone(result['proposal']['clarification_question'])

    def test_encoder_adds_diagnostics_without_altering_calibrated_joint_policy(self):
        from test_semantic_clarification import catalog
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            encoder=Encoder(Path(directory))
            encoder.load=Mock(return_value='FIXTURE_TECNICA')
            encoder.model=SimpleNamespace(encode=lambda texts,**kwargs:[[1.,0.,0.] for _ in texts])
            numpy=SimpleNamespace(dot=lambda a,b:sum(x*y for x,y in zip(a,b)))
            with patch.dict(sys.modules,{'numpy':numpy}):
                result=encoder.infer('acnde o ventilador',catalog(),'e5-small',{},context={'origin_area':'office'})
            self.assertEqual(result['analysis']['action']['text'],'acnde')
            self.assertIn('semantic_similarity',result['analysis']['action']['ranking'][0])
            self.assertEqual(result['analysis']['device']['entity_id'],'fan.office')
            self.assertEqual(result['analysis']['device']['text'],'Ventilador')
            self.assertEqual(result['decision_status'],'ambiguous')
            self.assertEqual(result['raw_score'],1)
            self.assertEqual(result['margin'],0)
            self.assertIsNone(result['proposal'])
            self.assertIsNone(result['confidence'])
