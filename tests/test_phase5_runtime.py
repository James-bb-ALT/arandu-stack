"""Apache-2.0 FIXTURE_TECNICA: real Rust HTTP, simulated authorized HA only."""
import asyncio
import copy
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

ROOT=Path(__file__).parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'arandu_stack/rootfs/modules/arandu_nlu/rootfs/src/tests/mlp')]
from test_contextual import hass_,input_,add
from phase4_scenarios import Http
from aiohttp import ClientSession
from custom_components.local_nlu.contextual_runtime import ContextualRuntime


@unittest.skipUnless(os.environ.get('ARANDU_NLU_ENDPOINT'),'Opt-in isolated real Rust HTTP')
class Phase5(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.http=ClientSession();self.addAsyncCleanup(self.http.close)
        self.hass=hass_();self.hass.async_create_task=asyncio.create_task
        self.options={'decision_enabled':True,'llm_enabled':True,'entity_preferences':{
            'sensor.ambient':{'measurement_kind':'ambient'},'sensor.hot':{'measurement_kind':'ambient'}}}
        self.hass.area_registry.areas['area_cozinha']=SimpleNamespace(name='Cozinha',aliases=set())
        add(self.hass,'auto','automation.irrigacao','Irrigação',{})
        add(self.hass,'list','todo.compras','Compras',{'supported_features':7})
        add(self.hass,'cinema','scene.cinema','Cinema',{})
        add(self.hass,'cinema_auto','automation.cinema','Cinema',{})
        add(self.hass,'kitchen','light.cozinha','Luz',{'supported_color_modes':['brightness'],'brightness':80},area='area_cozinha')
        add(self.hass,'ambient','sensor.ambient','Ambiente',{'device_class':'temperature','unit_of_measurement':'°C','state_class':'measurement'},state='24')
        add(self.hass,'hot','sensor.hot','Ambiente',{'device_class':'temperature','unit_of_measurement':'°C','state_class':'measurement'},state='28',area='area_cozinha')
        self.hass.services.available.update({('automation','turn_on'),('automation','turn_off'),('automation','trigger'),('todo','get_items'),('todo','add_item'),('todo','update_item'),('todo','remove_item'),('todo','remove_completed_items'),('scene','turn_on')})
        self.items=[{'uid':'coffee','summary':'Café','status':'needs_action'},{'uid':'milk','summary':'leite','status':'needs_action'},{'uid':'rice','summary':'arroz','status':'completed'}]
        self.reads=[];self.after_read=None
        async def call(domain,service,data,**kwargs):
            target=kwargs['target']['entity_id'][0]
            if domain=='todo' and service=='get_items':
                self.reads.append(kwargs)
                answer={target:{'items':copy.deepcopy(self.items)}}
                if self.after_read:self.after_read()
                return answer
            self.hass.services.calls.append({'domain':domain,'service':service,'data':copy.deepcopy(data),**kwargs})
            state=self.hass.states.get(target)
            if service in ('turn_on','turn_off'):state.state='on' if service=='turn_on' else 'off'
            if 'brightness_pct' in data:state.attributes['brightness']=round(data['brightness_pct']*255/100)
            if service=='set_percentage':state.attributes['percentage']=data['percentage']
            if domain=='todo' and service=='update_item':
                for item in self.items:
                    if item['uid']==data['item']:
                        if 'rename' in data:item['summary']=data['rename']
                        if 'status' in data:item['status']=data['status']
            if domain=='todo' and service=='remove_item':self.items[:]=[i for i in self.items if i['uid']!=data['item']]
            if domain=='todo' and service=='remove_completed_items':self.items[:]=[i for i in self.items if i['status']!='completed']
            return None
        self.hass.services.async_call=call
        self.runtime=ContextualRuntime(self.hass,Http(self.http),lambda:self.options)
        self.addAsyncCleanup(self.runtime.async_close)
        self.neural=AsyncMock(side_effect=AssertionError('Phase5 called neural transport'))
        self.patch=patch('custom_components.local_nlu.ai_lab.local_request',self.neural)
        self.patch.start();self.addCleanup(self.patch.stop)

    async def say(self,text,**kwargs):
        result=await self.runtime.process(input_(text,**kwargs))
        self.neural.assert_not_awaited()
        return result

    async def test_undo_snapshot_is_after_last_authentication_await(self):
        original=self.runtime.revalidate
        async def revalidate(*args,**kwargs):
            result=await original(*args,**kwargs)
            # An external user changed the state while authentication yielded.
            self.hass.states.get('light.sala').state='off'
            return result
        self.runtime.revalidate=revalidate
        self.assertEqual((await self.say('Desliga a luz da sala')).code,'success')
        self.runtime.revalidate=original
        result=await self.say('Desfaz')
        self.assertEqual(result.code,'success',result)
        self.assertEqual(self.hass.services.calls[-1]['service'],'turn_off')

    async def test_automation_enable_is_not_trigger_and_conditions_are_explicit(self):
        self.assertEqual((await self.say('Ativa a automação irrigação')).code,'success')
        self.assertEqual(self.hass.services.calls[-1]['service'],'turn_on')
        self.assertEqual((await self.say('Desliga a automação irrigação')).code,'success')
        self.assertEqual(self.hass.services.calls[-1]['service'],'turn_off')
        self.assertEqual((await self.say('Executa a automação irrigação')).code,'confirmation_required')
        self.assertEqual(len(self.hass.services.calls),2)
        wrong=await self.say('Sim',satellite_id='other')
        self.assertNotEqual(wrong.code,'success')
        result=await self.say('Sim')
        self.assertEqual(result.code,'success',result)
        self.assertEqual(self.hass.services.calls[-1]['data'],{'skip_condition':False})
        self.assertIn('não foram observadas',result.response_text)

    async def test_todo_complete_remove_update_bind_uid_and_preserve_context(self):
        for phrase,service,expected in [
            ('Marca café como comprado','update_item',{'item':'coffee','status':'completed'}),
            ('Troca leite por leite sem lactose na lista','update_item',{'item':'milk','rename':'leite sem lactose'}),
            ('Tira leite sem lactose da lista de compras','remove_item',{'item':'milk'})]:
            result=await self.say(phrase)
            self.assertEqual(result.code,'success',result)
            call=self.hass.services.calls[-1]
            self.assertEqual((call['domain'],call['service'],call['data']),('todo',service,expected))
            self.assertEqual(call['context'].user_id,'user')
            self.assertTrue(call['blocking'])
            self.assertEqual(call['target'],{'entity_id':['todo.compras']})

    async def test_duplicate_item_and_changed_authority_cannot_mutate(self):
        self.items.append({'uid':'coffee2','summary':'CAFÉ','status':'needs_action'})
        self.assertNotEqual((await self.say('Marca café como comprado')).code,'success')
        self.assertEqual(self.hass.services.calls,[])
        self.items.pop()
        self.after_read=lambda:self.hass.exposed.discard('todo.compras')
        self.assertNotEqual((await self.say('Tira leite da lista de compras')).code,'success')
        self.assertEqual(self.hass.services.calls,[])

    async def test_clear_completed_requires_fresh_single_use_confirmation(self):
        self.assertEqual((await self.say('Limpa os itens concluídos da lista')).code,'confirmation_required')
        self.items.append({'uid':'extra','summary':'Outro','status':'completed'})
        result=await self.say('Sim')
        self.assertNotEqual(result.code,'success')
        self.assertEqual(self.hass.services.calls,[])
        self.assertEqual((await self.say('Limpa os itens concluídos da lista')).code,'confirmation_required')
        self.assertEqual((await self.say('Sim')).code,'success')
        self.assertNotEqual((await self.say('Sim')).code,'success')
        self.assertEqual(len(self.hass.services.calls),1)
        self.assertEqual(self.hass.services.calls[0]['service'],'remove_completed_items')

    async def test_literal_list_content_never_becomes_device_instructions(self):
        text='Adiciona desligue a luz e abra a porta na lista de compras'
        result=await self.say(text)
        self.assertEqual(result.code,'success',result)
        self.assertEqual(self.hass.services.calls[0]['data'],{'item':'desligue a luz e abra a porta'})
        self.assertEqual(self.hass.services.calls[0]['domain'],'todo')

    async def test_ellipsis_and_undo_are_scoped_and_compare_current_state(self):
        self.assertEqual((await self.say('Liga a luz da sala')).code,'success')
        self.assertEqual((await self.say('Agora a da cozinha')).code,'success')
        self.assertEqual(self.hass.services.calls[-1]['target'],{'entity_id':['light.cozinha']})
        self.assertNotEqual((await self.say('Desfaz',conversation_id='other')).code,'success')
        self.assertEqual((await self.say('Desfaz')).code,'success')
        self.assertEqual(self.hass.states.get('light.cozinha').state,'off')
        self.assertEqual((await self.say('Deixa a luz da sala em 40%')).code,'success')
        self.hass.states.get('light.sala').attributes['brightness']=200
        before=len(self.hass.services.calls)
        self.assertNotEqual((await self.say('Volta como estava')).code,'success')
        self.assertEqual(len(self.hass.services.calls),before)

    async def test_alias_scene_and_ambient_rank_and_no_unsafe_fallback(self):
        self.assertEqual((await self.say('Modo cinema')).code,'success')
        self.assertEqual(self.hass.services.calls[-1]['domain'],'scene')
        result=await self.say('Qual cômodo está mais quente?')
        self.assertEqual(result.code,'query_success',result)
        self.assertIn('28',result.response_text)
        for phrase in ['O outro também','Cria uma rotina para ligar o ventilador','Qual aparelho está puxando mais energia agora?','Se a temperatura passar de 28 graus liga o ventilador']:
            before=len(self.hass.services.calls)
            self.assertNotEqual((await self.say(phrase)).code,'success',phrase)
            self.assertEqual(len(self.hass.services.calls),before)

    async def test_missing_history_does_not_invent_consumption(self):
        add(self.hass,'energy','sensor.energy','Consumo',{'device_class':'energy','unit_of_measurement':'kWh','state_class':'total_increasing'},state='123')
        result=await self.say('Quanto consumiu ontem?')
        self.assertEqual(result.code,'unavailable',result)
        self.assertEqual(result.reason,'history_unavailable')
        self.assertEqual(self.hass.services.calls,[])

    async def test_multiple_lists_require_selection_and_exact_recent_list_is_usable(self):
        add(self.hass,'market','todo.mercado','Mercado',{'supported_features':7})
        self.assertEqual((await self.say('Marca café como comprado')).code,'missing_slot')
        self.assertEqual(self.hass.services.calls,[])
        self.assertEqual((await self.say('Mostra os itens da lista de compras')).code,'query_success')
        self.assertEqual((await self.say('Marca café como comprado')).code,'success')
        self.assertEqual(self.hass.services.calls[-1]['target'],{'entity_id':['todo.compras']})

    async def test_exposed_scene_homonyms_and_move_feature_do_not_authorize_clear(self):
        add(self.hass,'cinema2','scene.cinema2','Cinema',{})
        self.assertNotEqual((await self.say('Modo cinema')).code,'success')
        self.hass.states.get('todo.compras').attributes['supported_features']=8
        self.assertNotEqual((await self.say('Limpa os itens concluídos da lista')).code,'success')
        self.assertEqual(self.hass.services.calls,[])

    async def test_context_revocation_and_adversarial_turn_clear_antecedent(self):
        self.assertEqual((await self.say('Liga a luz da sala')).code,'success')
        self.assertNotEqual((await self.say('Se ficar quente liga o ventilador')).code,'success')
        before=len(self.hass.services.calls)
        self.assertNotEqual((await self.say('Agora a da cozinha')).code,'success')
        self.assertEqual(len(self.hass.services.calls),before)
        self.runtime.options_changed()
        self.assertNotEqual((await self.say('Desfaz')).code,'success')

    async def test_other_target_only_when_exactly_one_compatible_alternative(self):
        add(self.hass,'fan2','fan.second','Segundo ventilador',{'supported_features':49,'percentage':30},area='area_quarto')
        self.hass.states.get('fan.ventilador').attributes['friendly_name']='Brisa'
        self.hass.entity_registry.entities['fan.ventilador'].name='Brisa'
        self.assertEqual((await self.say('Liga Brisa')).code,'success')
        self.assertEqual((await self.say('O outro também')).code,'success')
        self.assertEqual(self.hass.services.calls[-1]['target'],{'entity_id':['fan.second']})
        add(self.hass,'fan3','fan.third','Terceiro ventilador',{'supported_features':49,'percentage':30},area='area_quarto')
        self.assertEqual((await self.say('Liga Brisa')).code,'success')
        self.assertNotEqual((await self.say('O outro também')).code,'success')

    async def test_concurrent_renames_never_choose_a_stale_item(self):
        results=await asyncio.gather(self.say('Troca leite por leite A na lista',conversation_id='one'),
            self.say('Troca leite por leite B na lista',conversation_id='two'))
        self.assertEqual(sum(r.code=='success' for r in results),1)
        self.assertEqual(len(self.hass.services.calls),1)
        self.assertEqual(self.hass.services.calls[0]['data']['item'],'milk')

    async def test_quoted_instructions_never_trigger_automation_or_scene(self):
        for phrase in ['"Executa a automação irrigação"',"Diga 'executa a automação irrigação'",'"Modo cinema"']:
            self.assertNotEqual((await self.say(phrase)).code,'success')
        self.assertEqual(self.hass.services.calls,[])

    async def test_slow_list_read_does_not_block_other_origin_and_cancels_without_effect(self):
        original=self.hass.services.async_call;entered=asyncio.Event();release=asyncio.Event()
        async def blocked(domain,service,data,**kwargs):
            if domain=='todo' and service=='get_items':entered.set();await release.wait()
            return await original(domain,service,data,**kwargs)
        self.hass.services.async_call=blocked
        task=asyncio.create_task(self.say('Tira leite da lista de compras',conversation_id='list'))
        await asyncio.wait_for(entered.wait(),1)
        result=await asyncio.wait_for(self.say('Liga a luz da sala',conversation_id='light'),1)
        self.assertEqual(result.code,'success')
        self.assertFalse(task.done())
        self.assertEqual((await self.say('Cancela',conversation_id='list')).code,'cancelled')
        self.assertEqual((await task).code,'cancelled')
        release.set()
        self.assertEqual([c['domain'] for c in self.hass.services.calls],['light'])
