"""FIXTURE_TECNICA: production routing and HA authorization, no model downloads."""
import asyncio
import copy
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import AsyncMock, patch

# Reuse the existing HA fixture and exercise the public HACS source tree.
sys.path.append(str(Path(__file__).parents[1] / 'arandu_stack/rootfs/modules/arandu_nlu/rootfs/src/tests/mlp'))
from test_contextual import Client, hass_, input_, operation, plan
from custom_components.local_nlu.contextual_runtime import ContextualRuntime
from custom_components.local_nlu.ai_lab_core import digest, pipeline
from custom_components.local_nlu.intelligence import eligible
from custom_components.local_nlu.contextual_protocol import Outcome


def proposal(entity='light.sala', action='turn_on'):
    return dict(intent=action, target_entities=[entity], target_area=None, confidence=None,
                requires_clarification=False, clarification_question=None, reason='FIXTURE_TECNICA')


class Routing(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass = hass_()
        self.options = dict(stack_endpoint='http://127.0.0.1:8770')
        self.client = Client({'version': 4, 'status': 'no_match'})
        self.runtime = ContextualRuntime(self.hass, self.client, lambda: self.options)
        self.addCleanup(self.runtime.close)
        self.calls = []
        self.decision_status = 'accepted'
        self.entity = 'light.sala'
        self.action = 'turn_on'
        self.after = None
        helper = ModuleType('homeassistant.helpers.aiohttp_client')
        helper.async_get_clientsession = lambda hass: None
        self.helper = patch.dict(sys.modules, {'homeassistant.helpers.aiohttp_client': helper})
        self.helper.start()
        self.addCleanup(self.helper.stop)
        self.request = patch('custom_components.local_nlu.ai_lab.local_request', self.infer)
        self.request.start()
        self.addCleanup(self.request.stop)

    async def infer(self, session, endpoint, path, *, payload, **kwargs):
        self.calls.append(payload['action'])
        if self.after:
            self.after()
        p = proposal(self.entity, self.action)
        ranking = [dict(entity_id=self.entity, intent=self.action, raw_score=.8)]
        if payload['action'] == 'decision':
            p['confidence'] = .9
            return dict(decision_status=self.decision_status, confidence=.9, margin=.2,
                        catalog_fingerprint=digest(payload['catalog']), ranking=ranking, proposal=p)
        return dict(proposal=p, retrieval=dict(ranking=ranking))

    def policy(self, decision, llm):
        self.options.update(decision_enabled=decision, llm_enabled=llm)

    async def test_all_four_policies(self):
        for decision, llm, calls, code in [(False,False,[],'no_match'), (True,False,['decision'],'success'),
                                         (False,True,['llm'],'success'), (True,True,['decision'],'success')]:
            with self.subTest(decision=decision, llm=llm):
                self.policy(decision,llm)
                self.runtime.sessions.clear()
                self.calls.clear()
                result = await self.runtime.process(input_())
                self.assertEqual(result.code,code)
                self.assertEqual(self.calls,calls)

    async def test_nlu_success_skips_models_for_all_policies(self):
        self.client.response = plan(operation())
        for decision, llm in [(False,False),(True,False),(False,True),(True,True)]:
            self.policy(decision,llm)
            self.runtime.sessions.clear()
            self.assertEqual((await self.runtime.process(input_())).code,'success')
        self.assertEqual(self.calls,[])

    async def test_rejection_routes_only_to_enabled_llm(self):
        self.decision_status='uncalibrated'
        for llm in (False,True):
            self.calls.clear()
            self.policy(True,llm)
            result=await self.runtime.process(input_())
            self.assertEqual(self.calls,['decision','llm'] if llm else ['decision'])
            self.assertEqual(result.code,'success' if llm else 'no_match')

    async def test_missing_identity_never_calls_models(self):
        self.policy(True,True)
        result=await self.runtime.process(input_(user_id=None))
        self.assertNotEqual(result.code,'success')
        self.assertEqual(self.calls,[])
        self.assertEqual(self.hass.services.calls,[])

    async def test_clarification_never_calls_models(self):
        self.policy(True,True)
        self.client.response={'version':4,'status':'clarification','reason':'ambiguous_target','candidates':['reg_light']}
        self.assertEqual((await self.runtime.process(input_())).code,'missing_slot')
        self.assertEqual(self.calls,[])

    async def test_invalid_target_or_action_never_executes(self):
        self.policy(False,True)
        for entity, action in [('light.missing','turn_on'),('light.sala','shell')]:
            self.entity,self.action=entity,action
            self.assertNotEqual((await self.runtime.process(input_())).code,'success')
        self.assertEqual(self.hass.services.calls,[])

    async def test_unexposed_and_denied_entities_never_execute(self):
        self.policy(False,True)
        self.hass.exposed.remove('light.sala')
        self.assertNotEqual((await self.runtime.process(input_())).code,'success')
        self.hass.exposed.add('light.sala')
        self.hass.auth.user.is_admin=False
        self.hass.auth.user.permissions.allowed={('light.sala','read')}
        self.assertNotEqual((await self.runtime.process(input_())).code,'success')
        self.assertEqual(self.hass.services.calls,[])

    async def test_revoked_exposure_during_inference(self):
        self.policy(False,True)
        self.after=lambda:self.hass.exposed.remove('light.sala')
        self.assertNotEqual((await self.runtime.process(input_())).code,'success')
        self.assertEqual(self.hass.services.calls,[])

    async def test_service_failure_can_reach_llm(self):
        self.policy(True,True)
        normal=self.infer
        async def unavailable(session,endpoint,path,*,payload,**kwargs):
            if payload['action']=='decision':
                self.calls.append('decision')
                raise ConnectionError('FIXTURE_TECNICA')
            return await normal(session,endpoint,path,payload=payload)
        with patch('custom_components.local_nlu.ai_lab.local_request',unavailable):
            self.assertEqual((await self.runtime.process(input_())).code,'success')
        self.assertEqual(self.calls,['decision','llm'])

    async def test_llm_ambiguous_name_never_uses_top_score_as_authorization(self):
        from test_contextual import add
        self.policy(False,True)
        add(self.hass,'other_light','light.other','Luz da sala',{'supported_color_modes':['onoff']})
        self.assertNotEqual((await self.runtime.process(input_(text='leva a luz da sala'))).code,'success')
        self.assertEqual(self.hass.services.calls,[])

    async def test_catalog_or_policy_changed_during_inference(self):
        self.policy(False,True)
        for change in [lambda:self.options.update(llm_enabled=False),
                       lambda:self.hass.states._states['light.sala'].attributes.update(friendly_name='changed'),
                       lambda:setattr(self.hass.states._states['light.sala'],'state','unavailable')]:
            self.policy(False,True)
            self.after=change
            self.assertNotEqual((await self.runtime.process(input_())).code,'success')
        self.assertEqual(self.hass.services.calls,[])

    async def test_timeout_does_not_execute(self):
        self.policy(True,True)
        async def slow(*args,**kwargs): await asyncio.sleep(.02)
        with patch('custom_components.local_nlu.intelligence.TIMEOUT',.001), patch('custom_components.local_nlu.ai_lab.local_request',slow):
            self.assertNotEqual((await self.runtime.process(input_())).code,'success')
        self.assertEqual(self.hass.services.calls,[])

    async def test_cancellation_propagates_without_late_execution(self):
        self.policy(False,True)
        started=asyncio.Event()
        async def blocked(*args,**kwargs):
            started.set()
            await asyncio.Event().wait()
        with patch('custom_components.local_nlu.ai_lab.local_request',blocked):
            task=asyncio.create_task(self.runtime.process(input_()))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(self.hass.services.calls,[])

    async def test_duplicate_fallback_uses_existing_guard(self):
        self.policy(False,True)
        self.assertEqual((await self.runtime.process(input_())).code,'success')
        self.assertEqual((await self.runtime.process(input_())).code,'cancelled')
        self.assertEqual(len(self.hass.services.calls),1)

    async def test_disabled_request_does_not_depend_on_endpoint(self):
        self.options={}
        self.assertEqual((await self.runtime.process(input_())).code,'no_match')
        self.assertEqual(self.calls,[])

    async def test_llm_lab_isolation(self):
        async def forbidden(_): self.fail('LLM-only called another model')
        result=await pipeline('FIXTURE_TECNICA',forbidden,forbidden,AsyncMock(return_value=proposal()),mode='llm')
        self.assertEqual(result['source'],'llm')
        self.assertEqual(result['stages']['decision']['status'],'skipped')

    def test_security_statuses_are_not_semantic_fallback(self):
        for status,reason in [('invalid_request',None),('stale',None),('unavailable',None),
                              ('clarification','ambiguous_target'),('no_match','permission_denied'),('no_match','no_compatible_capability')]:
            self.assertFalse(eligible(Outcome(status,reason=reason)))
