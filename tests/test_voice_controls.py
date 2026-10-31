"""FIXTURE_TECNICA: origin output controls and cancellation without model inference."""
import asyncio
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.path.append(str(Path(__file__).parents[1]/'arandu_stack/rootfs/modules/arandu_nlu/rootfs/src/tests/mlp'))
from test_contextual import Client, hass_, input_, add, plan, operation
from custom_components.local_nlu.contextual_runtime import ContextualRuntime, Session
from custom_components.local_nlu.voice_controls import cancellation, volume


class Parsing(unittest.TestCase):
    def test_volume_scale_and_explicit_percentage(self):
        for text, percent in [('volume 0',0),('volume 2',20),('volume três',30),
                ('volume 5',50),('volume dez',100),('volume 10%',10),
                ('volume cinco por cento',5),('coloca o volume em 50 por cento',50),
                ('ajusta seu volume para vinte e cinco por cento',25),('volume cem por cento',100)]:
            self.assertEqual(volume(text),(True,percent),text)
        for text in ['volume 11','volume -2','volume 101%','volume 3,5','volume 3.5']:
            self.assertEqual(volume(text),(True,None),text)
        for text in ['volume da TV','volume 3 da TV','liga a luz']:
            self.assertFalse(volume(text)[0],text)

    def test_cancel_abandoned_utterance_without_matching_preposition(self):
        for text in ['cancela','para','pare','deixa pra lá','nada','liga a luz cancela','liga a luz, deixa pra lá']:
            self.assertTrue(cancellation(text),text)
        for text in ['coloca volume para cinco','para a música da TV','qual a temperatura']:
            self.assertFalse(cancellation(text),text)


class OutputControls(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass=hass_()
        device=SimpleNamespace(area_id='area_sala',disabled_by=None,name='FIXTURE_SATELLITE',name_by_user=None)
        self.hass.device_registry=SimpleNamespace(async_get=lambda key:device if key=='voice1' else None)
        satellite=add(self.hass,'reg_satellite','assist_satellite.fixture','Satélite',{})
        satellite.device_id='voice1'
        speaker=add(self.hass,'reg_speaker','media_player.speaker','Saída de voz',
                    {'supported_features':4100,'volume_level':.5},state='playing')
        speaker.device_id='voice1'
        other=add(self.hass,'reg_tv','media_player.tv','TV',{'supported_features':4100,'volume_level':.5},state='playing')
        other.device_id='other_device'
        self.hass.services.available.add(('media_player','media_stop'))
        self.hass.services.available.add(('media_player','volume_set'))
        self.client=Client(plan(operation()))
        self.runtime=ContextualRuntime(self.hass,self.client,lambda:{})
        self.addCleanup(self.runtime.close)

    def request(self,text,**kwargs):
        return input_(text=text,satellite_id='assist_satellite.fixture',**kwargs)

    async def test_respeaker_misunderstanding_is_short_and_discards_only_own_dialogue(self):
        device = self.hass.device_registry.async_get('voice1')
        device.model = 'ReSpeaker Lite'
        request = self.request('linha TV do quarto')
        owner = ('user', 'assist_satellite.fixture', 'fixture')
        other = ('user', 'assist_satellite.other', 'fixture')
        for code in ('missing_slot', 'no_match', 'unsupported_intent', 'ambiguous', 'unresolved_target'):
            self.runtime.sessions[owner] = Session(9999999999, pending={'action': 'turn_on'})
            self.runtime.sessions[other] = Session(9999999999)
            result = self.runtime.result(code, request, speech='Explicação longa. Pode repetir?', followup=True)
            self.assertEqual((result.code, result.response_text, result.continue_conversation),
                             ('no_match', 'Não entendi.', False))
            self.assertNotIn(owner, self.runtime.sessions)
            self.assertIn(other, self.runtime.sessions)
        self.assertEqual(self.hass.services.calls, [])

    async def test_short_profile_uses_registered_hostname_and_preserves_other_results(self):
        device = self.hass.device_registry.async_get('voice1')
        device.configuration_url = 'http://respeaker-jaime.local'
        request = self.request('teste')
        self.assertEqual(self.runtime.result('no_match', request).response_text, 'Não entendi.')
        for code in ('success', 'query_success', 'confirmation_required', 'denied', 'cancelled', 'unavailable'):
            result = self.runtime.result(code, request, speech='FIXTURE', followup=True)
            self.assertEqual(result.response_text, 'FIXTURE')
            self.assertTrue(result.continue_conversation)
        request.satellite_id = None
        request.device_id = 'voice1'
        self.assertEqual(self.runtime.result('no_match', request).response_text, 'Não entendi.')

    async def test_other_devices_and_absent_origin_keep_clarifications(self):
        for satellite in ('assist_satellite.fixture', None, 'assist_satellite.unknown'):
            request = input_(satellite_id=satellite)
            result = self.runtime.result('missing_slot', request, speech='Qual alvo?', followup=True)
            self.assertEqual(result.response_text, 'Qual alvo?')
            self.assertTrue(result.continue_conversation)

    async def test_volume_controls_only_origin_without_calling_nlu(self):
        result=await self.runtime.process(self.request('volume 3'))
        self.assertEqual(result.code,'success')
        self.assertEqual(self.client.requests,[])
        call=self.hass.services.calls[-1]
        self.assertEqual(call['service'],'volume_set')
        self.assertEqual(call['data'],{'volume_level':.3})
        self.assertEqual(call['target'],{'entity_id':['media_player.speaker']})

    async def test_percentage_preserves_small_percentages(self):
        result=await self.runtime.process(self.request('volume 5 por cento'))
        self.assertEqual(result.code,'success')
        self.assertEqual(self.hass.services.calls[-1]['data'],{'volume_level':.05})

    async def test_explicit_output_binding_supports_separate_integrations(self):
        self.runtime.options=lambda:{'voice_output_bindings':{'satellite:assist_satellite.fixture':'media_player.tv'}}
        result=await self.runtime.process(self.request('volume dois'))
        self.assertEqual(result.code,'success')
        self.assertEqual(self.hass.services.calls[-1]['target'],{'entity_id':['media_player.tv']})
        self.assertEqual(self.hass.services.calls[-1]['data'],{'volume_level':.2})

    async def test_device_binding_is_found_when_assist_sends_only_satellite(self):
        self.runtime.options=lambda:{'voice_output_bindings':{'device:voice1':'media_player.tv'}}
        result=await self.runtime.process(self.request('volume 3'))
        self.assertEqual(result.code,'success')
        self.assertEqual(self.hass.services.calls[-1]['target'],{'entity_id':['media_player.tv']})

    async def test_satellite_binding_is_found_when_assist_sends_only_device(self):
        self.runtime.options=lambda:{'voice_output_bindings':{'satellite:assist_satellite.fixture':'media_player.tv'}}
        request=input_(text='volume 3')
        request.device_id='voice1'
        self.assertEqual((await self.runtime.process(request)).code,'success')
        self.assertEqual(self.hass.services.calls[-1]['target'],{'entity_id':['media_player.tv']})

    async def test_conflicting_satellites_on_one_device_are_not_guessed(self):
        other=add(self.hass,'reg_sat2','assist_satellite.other','Outro',{})
        other.device_id='voice1'
        self.runtime.options=lambda:{'voice_output_bindings':{
            'satellite:assist_satellite.fixture':'media_player.tv',
            'satellite:assist_satellite.other':'media_player.speaker'}}
        request=input_(text='volume 3')
        request.device_id='voice1'
        result=await self.runtime.process(request)
        self.assertEqual(result.reason,'voice_output_ambiguous_binding')
        self.assertEqual(self.hass.services.calls,[])

    async def test_configured_output_does_not_fall_back_when_unexposed(self):
        self.runtime.options=lambda:{'voice_output_bindings':{'satellite:assist_satellite.fixture':'media_player.tv'}}
        self.hass.exposed.remove('media_player.tv')
        result=await self.runtime.process(self.request('volume dois'))
        self.assertEqual(result.code,'unavailable')
        self.assertEqual(result.reason,'voice_output_not_exposed')
        self.assertEqual(self.hass.services.calls,[])

    async def test_device_origin_without_satellite_entity(self):
        request=input_(text='volume 5')
        request.device_id='voice1'
        result=await self.runtime.process(request)
        self.assertEqual(result.code,'success')
        self.assertEqual(self.hass.services.calls[-1]['target'],{'entity_id':['media_player.speaker']})

    async def test_cancel_without_output_support_still_closes_dialogue(self):
        self.hass.services.available.remove(('media_player','media_stop'))
        result=await self.runtime.process(self.request('nada'))
        self.assertEqual(result.code,'cancelled')
        self.assertEqual(result.reason,'satellite_playback_stop_unavailable')
        self.assertEqual(self.hass.services.calls,[])

    async def test_cancel_uses_pause_when_stop_is_not_supported(self):
        self.hass.services.available.remove(('media_player','media_stop'))
        self.hass.services.available.add(('media_player','media_pause'))
        self.hass.states._states['media_player.speaker'].attributes['supported_features']=5
        result=await self.runtime.process(self.request('para'))
        self.assertEqual(result.reason,'satellite_playback_stopped')
        self.assertEqual(self.hass.services.calls[-1]['service'],'media_pause')

    async def test_no_origin_or_ambiguous_output_never_controls_tv(self):
        result=await self.runtime.process(input_(text='volume 3'))
        self.assertEqual(result.code,'unavailable')
        self.hass.entity_registry.entities['media_player.tv'].device_id='voice1'
        result=await self.runtime.process(self.request('volume 3'))
        self.assertEqual(result.code,'unavailable')
        self.assertEqual(self.hass.services.calls,[])

    async def test_unexposed_output_and_invalid_volume_have_no_effect(self):
        self.hass.exposed.remove('media_player.speaker')
        self.assertEqual((await self.runtime.process(self.request('volume 3'))).code,'unavailable')
        self.assertEqual((await self.runtime.process(self.request('volume 30'))).code,'missing_slot')
        self.assertEqual(self.hass.services.calls,[])

    async def test_cancel_clears_all_dialogues_for_origin_and_stops_only_its_speaker(self):
        for conv in ['fixture','previous']:
            self.runtime.sessions[('user','assist_satellite.fixture',conv)]=Session(float('inf'),pending={'fixture':True})
        self.runtime.sessions[('user','assist_satellite.other','fixture')]=Session(float('inf'))
        result=await self.runtime.process(self.request('deixa pra lá'))
        self.assertEqual(result.code,'cancelled')
        self.assertEqual(result.response_text,'')
        self.assertFalse(result.continue_conversation)
        self.assertEqual(len(self.runtime.sessions),1)
        self.assertEqual(self.hass.services.calls[-1]['service'],'media_stop')
        self.assertEqual(self.hass.services.calls[-1]['target'],{'entity_id':['media_player.speaker']})
        self.assertEqual(self.client.requests,[])

    async def test_cancel_interrupts_active_inference_and_discards_queued_utterance(self):
        entered=asyncio.Event()
        async def blocked(payload):
            entered.set()
            await asyncio.Event().wait()
        self.client.async_interpret_v4=AsyncMock(side_effect=blocked)
        first=asyncio.create_task(self.runtime.process(self.request('liga a luz')))
        await asyncio.wait_for(entered.wait(),1)
        queued=asyncio.create_task(self.runtime.process(self.request('liga a luz',conversation_id='queued')))
        await asyncio.sleep(0)
        cancel=await asyncio.wait_for(self.runtime.process(self.request('para')),1)
        self.assertEqual(cancel.code,'cancelled')
        self.assertEqual((await first).code,'cancelled')
        self.assertEqual((await queued).code,'cancelled')
        self.client.async_interpret_v4.assert_awaited_once()
        self.assertEqual([c['service'] for c in self.hass.services.calls],['media_stop'])

    async def test_unauthorized_cancel_does_not_stop_audio(self):
        self.hass.auth.user=None
        result=await self.runtime.process(self.request('cancela',user_id='unknown'))
        self.assertEqual(result.code,'denied')
        self.assertEqual(self.hass.services.calls,[])

    async def test_read_only_user_cannot_change_volume(self):
        self.hass.auth.user.is_admin=False
        self.hass.auth.user.permissions.allowed={('media_player.speaker','read')}
        self.assertEqual((await self.runtime.process(self.request('volume 3'))).code,'unavailable')
        self.assertEqual(self.hass.services.calls,[])

    async def test_cancel_does_not_wait_for_or_cancel_another_satellite(self):
        other=add(self.hass,'reg_other_sat','assist_satellite.other','Outro satélite',{})
        other.device_id='other_device'
        entered=asyncio.Event()
        async def blocked(payload):
            entered.set()
            await asyncio.Event().wait()
        self.client.async_interpret_v4=AsyncMock(side_effect=blocked)
        active=asyncio.create_task(self.runtime.process(input_(text='liga a luz',satellite_id='assist_satellite.other')))
        await asyncio.wait_for(entered.wait(),1)
        try:
            result=await asyncio.wait_for(self.runtime.process(self.request('para')),1)
            self.assertEqual(result.code,'cancelled')
            self.assertFalse(active.done())
            self.assertEqual(self.hass.services.calls[-1]['target'],{'entity_id':['media_player.speaker']})
        finally:
            active.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await active

    async def test_cancel_never_queries_stack_or_expands_custom_phrase(self):
        from custom_components.local_nlu.stack import prepare
        entry=SimpleNamespace(options={'stack_endpoint':'http://127.0.0.1:8770'})
        request=self.request('liga a luz deixa pra lá')
        prepared,problem=await prepare(self.hass,entry,request)
        self.assertIs(prepared,request)
        self.assertIsNone(problem)


class OutputConfiguration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from test_nlu_discovery_flow import NluDiscoveryFlow
        NluDiscoveryFlow.setUpClass()
        self.module=NluDiscoveryFlow.module
        from custom_components.local_nlu import voice_controls, identity
        namespace=ModuleType('_arandu_flow_fixture')
        namespace.__path__=[]
        self.imports=patch.dict(sys.modules,{'_arandu_flow_fixture':namespace,
            '_arandu_flow_fixture.voice_controls':voice_controls,'_arandu_flow_fixture.identity':identity})
        self.imports.start()
        self.addCleanup(self.imports.stop)
        class Field:
            def __init__(self,name,**kwargs):
                self.name,self.default=name,kwargs.get('default')
        self.schema=patch.object(self.module,'vol',SimpleNamespace(Optional=Field,Schema=lambda fields:fields))
        self.selectors=patch.object(self.module,'selector',SimpleNamespace(
            EntitySelector=lambda value:value,EntitySelectorConfig=dict,
            DeviceSelector=lambda:{},SelectSelector=lambda value:value,
            SelectSelectorConfig=dict,SelectSelectorMode=SimpleNamespace(DROPDOWN='dropdown')))
        self.schema.start();self.selectors.start()
        self.addCleanup(self.schema.stop);self.addCleanup(self.selectors.stop)

    async def test_bind_and_remove_output_preserves_other_options(self):
        flow=self.module.LocalNluOptionsFlow()
        flow.config_entry=SimpleNamespace(options={'decision_enabled':True})
        flow.hass=hass_()
        add(flow.hass,'reg_sat','assist_satellite.fixture','Satélite',{})
        add(flow.hass,'reg_output','media_player.speaker','Saída',{'supported_features':4})
        flow.hass.services.available.add(('media_player','volume_set'))
        result=await flow.async_step_voice_output({'satellite_id':'assist_satellite.fixture','output_player':'media_player.speaker','save_and_finish':True})
        self.assertEqual(result['type'],'create_entry')
        self.assertTrue(result['data']['decision_enabled'])
        self.assertEqual(result['data']['voice_output_bindings'],{'satellite:assist_satellite.fixture':'media_player.speaker'})
        flow.config_entry.options=result['data']
        result=await flow.async_step_voice_output({'existing_binding':'satellite:assist_satellite.fixture','save_and_finish':True})
        self.assertEqual(result['data']['voice_output_bindings'],{})
        self.assertTrue(result['data']['decision_enabled'])

    async def test_audio_batch_displays_associations_then_empty_submit_saves(self):
        flow=self.module.LocalNluOptionsFlow()
        flow.config_entry=SimpleNamespace(options={'decision_enabled':True})
        flow.hass=hass_()
        flow.hass.services.available.add(('media_player','volume_set'))
        for i in range(2):
            add(flow.hass,f'sat{i}',f'assist_satellite.fixture{i}',f'Satélite {i}',{})
            add(flow.hass,f'output{i}',f'media_player.fixture{i}',f'Saída {i}',{'supported_features':4})
            result=await flow.async_step_voice_output({'satellite_id':f'assist_satellite.fixture{i}','output_player':f'media_player.fixture{i}'})
            self.assertEqual(result['type'],'form')
            for j in range(i+1):
                self.assertIn(f'assist\\_satellite.fixture{j}',result['description_placeholders']['bindings'])
            self.assertFalse(next(k.default for k in result['data_schema'] if k.name=='save_and_finish'))
        self.assertNotIn('voice_output_bindings',flow.config_entry.options)  # No partial save.
        result=await flow.async_step_voice_output({})
        self.assertEqual(result['type'],'create_entry')
        self.assertEqual(len(result['data']['voice_output_bindings']),2)
        self.assertTrue(result['data']['decision_enabled'])

    async def test_setup_rejects_unexposed_output_and_lists_existing_problem(self):
        flow=self.module.LocalNluOptionsFlow()
        flow.config_entry=SimpleNamespace(options={'voice_output_bindings':{'satellite:assist_satellite.fixture':'media_player.speaker'}})
        flow.hass=hass_()
        add(flow.hass,'sat','assist_satellite.fixture','Satélite',{})
        add(flow.hass,'out','media_player.speaker','Saída',{'supported_features':4})
        flow.hass.exposed.remove('media_player.speaker')
        result=await flow.async_step_voice_output({'satellite_id':'assist_satellite.fixture','output_player':'media_player.speaker'})
        self.assertEqual(result['errors']['base'],'voice_output_not_exposed')
        self.assertIn('não exposta',result['description_placeholders']['bindings'])

    async def test_identity_batch_default_is_continue_and_blank_finishes(self):
        flow=self.module.LocalNluOptionsFlow()
        flow.config_entry=SimpleNamespace(options={'llm_enabled':True})
        flow.hass=hass_()
        flow._active_users=AsyncMock(return_value={'user':SimpleNamespace(name='Usuário fixture')})
        for i in range(2):
            add(flow.hass,f'sat{i}',f'assist_satellite.fixture{i}',f'Satélite {i}',{})
            result=await flow.async_step_identity_binding({'satellite_id':f'assist_satellite.fixture{i}','identity_user_id':'user'})
            self.assertEqual(result['type'],'form')
            self.assertFalse(next(k.default for k in result['data_schema'] if k.name=='save_and_finish'))
            self.assertIn('Usuário fixture',result['description_placeholders']['bindings'])
        result=await flow.async_step_identity_binding({})
        self.assertEqual(len(result['data']['voice_identity_bindings']),2)
        self.assertTrue(result['data']['llm_enabled'])

    async def test_menu_opens_audio_and_identity_directly_without_checkbox(self):
        flow=self.module.LocalNluOptionsFlow()
        result=await flow.async_step_init()
        self.assertEqual(result['type'],'menu')
        self.assertEqual(result['menu_options'],['voice_output','identity_binding','intelligence','settings'])
