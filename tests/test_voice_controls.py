"""FIXTURE_TECNICA: origin output controls and cancellation without model inference."""
import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

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

    async def test_configured_output_does_not_fall_back_when_unexposed(self):
        self.runtime.options=lambda:{'voice_output_bindings':{'satellite:assist_satellite.fixture':'media_player.tv'}}
        self.hass.exposed.remove('media_player.tv')
        result=await self.runtime.process(self.request('volume dois'))
        self.assertEqual(result.code,'unavailable')
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
    async def test_bind_and_remove_output_preserves_other_options(self):
        from test_nlu_discovery_flow import NluDiscoveryFlow
        NluDiscoveryFlow.setUpClass()
        flow=NluDiscoveryFlow.module.LocalNluOptionsFlow()
        flow.config_entry=SimpleNamespace(options={'decision_enabled':True})
        flow.hass=hass_()
        add(flow.hass,'reg_sat','assist_satellite.fixture','Satélite',{})
        add(flow.hass,'reg_output','media_player.speaker','Saída',{'supported_features':4})
        result=await flow.async_step_voice_output({'satellite_id':'assist_satellite.fixture','output_player':'media_player.speaker'})
        self.assertEqual(result['type'],'create_entry')
        self.assertTrue(result['data']['decision_enabled'])
        self.assertEqual(result['data']['voice_output_bindings'],{'satellite:assist_satellite.fixture':'media_player.speaker'})
        flow.config_entry.options=result['data']
        result=await flow.async_step_voice_output({'satellite_id':'assist_satellite.fixture','remove_binding':True})
        self.assertEqual(result['data']['voice_output_bindings'],{})
        self.assertTrue(result['data']['decision_enabled'])
