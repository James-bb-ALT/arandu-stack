"""Approved Phase 3 examples / technical fixtures; not representative speech gold."""
import copy
import json
import tempfile
import unittest
import asyncio
import hashlib
import io
import sys
import os
import time
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'arandu_stack/rootfs/modules/arandu_llm/rootfs/src/src'))

import test_semantic_confirmation as fixtures
from custom_components.local_nlu.automation_contract import analyze,validate
from custom_components.local_nlu.intelligence import typed_operations
from custom_components.local_nlu.ai_lab_core import digest
from custom_components.local_nlu.capabilities import parameters,ADAPTERS,CapabilityError


def catalog():
    specs=[('light.sala','light','Luz',['Iluminação'],'sala',['turn_on','turn_off','brightness','color','query']),
        ('light.cozinha','light','Luz',[],'cozinha',['turn_on','turn_off','brightness','query']),
        ('climate.sala','climate','Ar',['Ar-condicionado'],'sala',['turn_on','turn_off','temperature','hvac_mode','fan_mode','query']),
        ('fan.sala','fan','Ventilador',[],'sala',['turn_on','turn_off','percentage','oscillate','direction','query']),
        ('switch.tv','switch','TV',['Televisão'],'sala',['turn_on','turn_off','query']),
        ('cover.sala','cover','Persiana',[],'sala',['open','close','position','query']),
        ('media_player.sala','media_player','Som',[],'sala',['play','pause','stop','next','previous','volume','query']),
        ('vacuum.casa','vacuum','Aspirador',[],'sala',['vacuum_start','vacuum_pause','vacuum_dock','query']),
        ('scene.noite','scene','Cena noite',[],'sala',['activate']),
        ('automation.noite','automation','Automação noite',[],'sala',['automation_enable','automation_disable']),
        ('sensor.temperatura','sensor','Temperatura',[],'quarto',['query'])]
    return {'version':4,'generation':'fixture','areas':[{'area_id':a,'names':[a.title()]} for a in ['sala','cozinha','quarto']],
        'entities':[{'entity_id':e,'registry_id':e.replace('.','_'),'domain':d,'name':n,'aliases':al,'area_id':a,
            'actions':ac,'attributes':{'available':True,'hvac_modes':['cool','heat','auto'],'fan_modes':['low','high']},
            'device_class':'temperature' if d=='sensor' else None} for e,d,n,al,a,ac in specs]}


def wire(analysis):
    return {k:analysis[k] for k in ('version','type','operations','question','response_text')}


CASES=[('Coloca a luz da sala em 40%','brightness','light.sala',{'value':40}),
    ('Deixa o ar em 23 graus','temperature','climate.sala',{'value':23}),
    ('Aumenta a iluminação em 10%','brightness','light.sala',{'value':10,'relative':1}),
    ('Abre a persiana até a metade','position','cover.sala',{'value':50}),
    ('Coloca a iluminação azul','color','light.sala',{'value':'azul'}),
    ('Coloca o ar no modo frio','hvac_mode','climate.sala',{'value':'cool'}),
    ('Coloca o ventilador em 50%','percentage','fan.sala',{'value':50}),
    ('Aumenta a temperatura do ar em 2 graus','temperature','climate.sala',{'value':2,'relative':1}),
    ('Liga a televisão','turn_on','switch.tv',{}),('Fecha a persiana','close','cover.sala',{}),
    ('Pausa o som','pause','media_player.sala',{}),('Coloca o volume do som em 30%','volume','media_player.sala',{'value':30}),
    ('Inicia o aspirador','vacuum_start','vacuum.casa',{}),('Manda o aspirador para a base','vacuum_dock','vacuum.casa',{}),
    ('Ativa a cena noite','activate','scene.noite',{}),
    ('Ativa a automação noite','automation_enable','automation.noite',{}),
    ('Coloca a direção do ventilador para frente','direction','fan.sala',{'value':'forward'}),
    ('Oscila o ventilador','oscillate','fan.sala',{'value':True}),
    ('Qual a temperatura do quarto?','query','sensor.temperatura',{'metric':'temperature'})]


class Contract(unittest.TestCase):
    def test_complete_catalog_tail_and_ambiguous_targets(self):
        data=catalog();template=data['entities'][3]
        data['entities']=[{**template,'entity_id':f'fan.fixture_{i}','registry_id':f'fixture_{i}','name':f'Device {i}','aliases':[]} for i in range(1024)]
        result=analyze('Liga Device 1023',data,{'origin_area':'sala'})
        self.assertEqual(result['operations'][0]['entity_id'],'fan.fixture_1023')
        data['entities'].append({**data['entities'][-1],'entity_id':'fan.other','registry_id':'other'})
        self.assertEqual(analyze('Liga Device 1023',data,{'origin_area':'sala'})['type'],'clarification')
        data=catalog();data['entities'][0].update(name='Abajur',aliases=[])
        result=analyze('Coloca o Abajur em 40%',data,{'origin_area':'sala'})
        self.assertEqual(result['operations'][0]['action'],'brightness')
    def test_approved_typed_families(self):
        for text,action,entity,params in CASES:
            with self.subTest(text=text):
                result=analyze(text,catalog(),{'origin_area':'sala'})
                self.assertEqual(result['type'],'automation',result)
                self.assertEqual(result['operations'],[{'action':action,'entity_id':entity,'parameters':params,'evidence':0}])
                validate(wire(result),text,catalog(),{'origin_area':'sala'})

    def test_compounds_order_and_explicit_room_ellipsis(self):
        for text,actions,entities in [
            ('Liga a TV e apaga a luz da sala',['turn_on','turn_off'],['switch.tv','light.sala']),
            ('Coloca o ar em 23 graus e liga o ventilador',['temperature','turn_on'],['climate.sala','fan.sala']),
            ('Apaga a luz da sala e da cozinha',['turn_off','turn_off'],['light.sala','light.cozinha'])]:
            result=analyze(text,catalog(),{'origin_area':'sala'})
            self.assertEqual(result['type'],'automation',result)
            self.assertEqual([o['action'] for o in result['operations']],actions)
            self.assertEqual([o['entity_id'] for o in result['operations']],entities)

    def test_vague_unsafe_ranges_and_contradictions_abstain(self):
        for text in ['Diminui o ventilador um pouco','Agora deixa ela mais fraca',
            'Aumenta o ventilador e diminui a luz','Coloca a luz em 140%',
            'Deixa o ar entre 20 e 25 graus','Desliga tudo menos a TV','Se estiver quente liga o ar',
            'Liga a TV ou o ventilador','Não liga nenhuma luz','Jamais desligue o ventilador',
            'Abre o portão e desativa o alarme','Liga a TV e desliga a TV',
            'Liga a TV então desliga a TV','Liga a TV, desliga a TV','Liga a TV desliga a TV',
            'Ativa a automação noite e desliga a automação noite','Inicia o aspirador e manda o aspirador para a base',
            'Liga a luz azul','Apaga a luz em 40%','Liga o ar dependendo do calor','Liga o ventilador silenciosamente',
            'Liga o ventilador do banheiro']:
            result=analyze(text,catalog(),{'origin_area':'sala'})
            self.assertEqual(result['type'],'clarification',(text,result))
            self.assertEqual(result['operations'],[])

    def test_conversation_has_no_operations(self):
        for text in ['O que é uma lâmpada?','Quem inventou o ar-condicionado?',
                     'Como faço para ligar o ventilador?','Conta uma piada']:
            self.assertEqual(analyze(text,catalog())['type'],'conversation',text)

    def test_history_uses_only_authorized_unique_last_device_and_action(self):
        context={'origin_area':'cozinha','last_targets':['climate_sala'],'last_area':'sala','last_action':'temperature'}
        result=analyze('Aumenta mais dois',catalog(),context)
        self.assertEqual(result['type'],'automation',result)
        self.assertEqual(result['operations'][0]['parameters'],{'relative':1,'value':2})
        self.assertEqual(result['operations'][0]['entity_id'],'climate.sala')
        self.assertEqual(analyze('Aumenta mais dois',catalog())['type'],'clarification')
        self.assertEqual(analyze('Deixa o ar em vinte e três graus',catalog(),{'origin_area':'sala'})['operations'][0]['parameters'],{'value':23})
        data=catalog();data['entities'].append({**data['entities'][3],'entity_id':'fan.quarto','registry_id':'fan_quarto','area_id':'quarto'})
        result=analyze('Desliga o da sala',data,{'origin_area':'quarto','last_targets':['fan_quarto'],'last_area':'quarto','last_action':'turn_on'})
        self.assertEqual(result['operations'][0]['entity_id'],'fan.sala')

    def test_descriptions_are_not_imperatives(self):
        for text in ['A luz da sala consome 40 watts','Ventilador em 40%',
                     'Conta uma história que liga a TV','Explique como coloca o ar em 23 graus']:
            self.assertNotEqual(analyze(text,catalog(),{'origin_area':'sala'})['type'],'automation',text)

    def test_units_must_match_ha_and_numeric_property(self):
        for text in ['Deixa o ar em 23 °F','Coloca a iluminação em 40 watts','Coloca o ventilador em 40 rpm','Deixa o ar em 23%']:
            self.assertEqual(analyze(text,catalog(),{'origin_area':'sala','temperature_unit':'°C'})['type'],'clarification',text)
        self.assertEqual(analyze('Deixa o ar em 23 °C',catalog(),{'origin_area':'sala','temperature_unit':'°C'})['type'],'automation')

    def test_forged_parameter_target_and_action_rejected_independently(self):
        data=catalog();text='Coloca a luz da sala em 40%'
        value=wire(analyze(text,data,{'origin_area':'sala'}))
        for key,replacement in [('parameters',{'value':70}),('entity_id','light.cozinha'),('action','turn_on'),('evidence',True)]:
            forged=copy.deepcopy(value);forged['operations'][0][key]=replacement
            with self.assertRaises(ValueError):validate(forged,text,data,{'origin_area':'sala'})
        snapshot=SimpleNamespace(payload=data,by_id={r['registry_id']:r for r in data['entities']})
        response={'contract_version':2,'plan':value,'catalog_fingerprint':digest(data)}
        op=typed_operations(response,text,snapshot,{'origin_area':'sala'})[0]
        self.assertEqual((op.intent,op.action,op.targets),('llm2.brightness','brightness',('light_sala',)))

    def test_shared_contract_is_identical(self):
        root=Path(__file__).resolve().parents[1]
        self.assertEqual((root/'custom_components/local_nlu/automation_contract.py').read_bytes(),
            (root/'arandu_stack/rootfs/modules/arandu_llm/rootfs/src/src/arandu_llm/automation_contract.py').read_bytes())

    def test_relative_limits_are_not_silently_clamped_for_llm2(self):
        state=SimpleNamespace(state='heat',attributes={'temperature':29,'min_temp':16,'max_temp':30,'target_temp_step':1})
        with self.assertRaises(CapabilityError):
            parameters('temperature',ADAPTERS['climate','temperature'],state,{'value':2,'relative':1},{},strict_numeric=True)
        self.assertEqual(parameters('temperature',ADAPTERS['climate','temperature'],state,{'value':2,'relative':-1},{},strict_numeric=True),{'temperature':27})


class Production(unittest.IsolatedAsyncioTestCase):
    say=fixtures.Confirmation.say

    async def asyncSetUp(self):
        await fixtures.Confirmation.asyncSetUp(self)
        fixtures.add(self.hass,'climate_fixture','climate.fixture','Ar',{'supported_features':385,'temperature':23,
            'current_temperature':24,'min_temp':16,'max_temp':30,'target_temp_step':1},area='area_quarto')
        self.hass.services.available.add(('climate','set_temperature'))

    async def infer(self,session,endpoint,path,*,payload,**kwargs):
        self.requests.append(payload)
        self.assertEqual(payload['action'],'llm')
        plan=analyze(payload['text'],payload['catalog'],payload.get('context'))
        return {'contract_version':2,'kind':plan['type'],'plan':wire(plan) if plan['type']=='automation' else plan,
            'catalog_fingerprint':digest(payload['catalog'])}

    async def test_typed_absolute_and_relative_reach_existing_executor(self):
        result=await self.say('Deixa o ar em 23 graus')
        self.assertEqual(result.code,'success',result)
        self.assertEqual(len(self.hass.services.calls),1)
        self.hass.states.get('climate.fixture').attributes['temperature']=24
        result=await self.say('Aumenta mais dois')
        self.assertEqual(result.code,'success',result)
        self.assertEqual(self.hass.services.calls[-1]['data']['temperature'],26)


    async def test_whole_plan_validated_before_first_effect(self):
        result=await self.say('Liga o ventilador e deixa o ar em 40 graus')
        self.assertNotEqual(result.code,'success')
        self.assertEqual(self.hass.services.calls,[])


    async def test_relative_range_and_step_fail_without_clamping(self):
        self.hass.states.get('climate.fixture').attributes['temperature']=29
        result=await self.say('Aumenta a temperatura do ar em 2 graus')
        self.assertNotEqual(result.code,'success')
        self.assertEqual(self.hass.services.calls,[])
        result=await self.say('Deixa o ar em 23,5 graus')
        self.assertNotEqual(result.code,'success')
        self.assertEqual(self.hass.services.calls,[])

    async def test_origin_change_invalidates_context(self):
        await self.say('Deixa o ar em 23 graus')
        fixtures.add(self.hass,'satellite_other','assist_satellite.other','Outro',{},area='area_sala')
        result=await self.runtime.process(fixtures.input_(text='Aumenta mais dois',satellite_id='assist_satellite.other'))
        self.assertNotEqual(result.code,'success')
        self.assertEqual(len(self.hass.services.calls),1)

    async def test_forged_parameters_and_revocation_never_execute(self):
        original=self.infer
        async def forged(*args,**kwargs):
            response=await original(*args,**kwargs)
            response['plan']['operations'][0]['parameters']['value']=28
            return response
        with patch('custom_components.local_nlu.ai_lab.local_request',forged):
            await self.say('Deixa o ar em 23 graus')
        self.assertEqual(self.hass.services.calls,[])
        async def revoked(*args,**kwargs):
            response=await original(*args,**kwargs)
            self.hass.auth.user.is_admin=False;self.hass.auth.user.permissions.allowed=set()
            return response
        with patch('custom_components.local_nlu.ai_lab.local_request',revoked):
            await self.say('Deixa o ar em 23 graus')
        self.assertEqual(self.hass.services.calls,[])

    async def test_late_response_has_no_effect(self):
        original=self.infer
        async def delayed(*args,**kwargs):
            await asyncio.sleep(.05)
            return await original(*args,**kwargs)
        with patch('custom_components.local_nlu.ai_lab.local_request',delayed),patch('custom_components.local_nlu.intelligence.TIMEOUT',.01):
            await self.say('Deixa o ar em 23 graus')
            await asyncio.sleep(.1)
        self.assertEqual(self.hass.services.calls,[])


class Migration(unittest.IsolatedAsyncioTestCase):
    async def test_profiles_persist_separately_and_legacy_modules_remain_readable(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'arandu_stack/app'))
        from modules import ModuleManager
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);manager=ModuleManager(root,local=True)
            manager.save()
            await manager.configure('llm',{'llm_model':'qwen3','threads':3,'max_tokens':256})
            await manager.configure('llm',{'llm_model':'tucano'})
            self.assertEqual(manager.config['llm']['max_tokens'],384)
            await manager.configure('llm',{'llm_model':'qwen3'})
            self.assertEqual(manager.config['llm']['threads'],3)
            saved=json.loads((root/'modules.json').read_text())
            self.assertNotIn('llm_model',saved['modules']['llm'])
            self.assertNotIn('contract_version',saved['modules']['llm'])
            restored=ModuleManager(root,local=True)
            self.assertEqual(restored.config['llm']['llm_model'],'qwen3')
            self.assertEqual(restored.config['llm']['max_tokens'],256)
            (root/'llm-policy.json').write_text('broken')
            recovered=ModuleManager(root,local=True)
            self.assertEqual(recovered.config['llm']['llm_model'],'tucano')
            self.assertEqual(recovered.config['llm']['contract_version'],1)
            self.assertIn('llm',recovered.failures)


class Installation(unittest.TestCase):
    def test_pinned_staging_verifies_and_recovers_without_promoting_bad_bytes(self):
        from arandu_llm.models import MODELS,install
        good=b'GGUF fixture valid';bad=b'GGUF fixture wrong'
        info={**MODELS['tucano'],'file':'fixture.gguf','size':len(good),'sha256':hashlib.sha256(good).hexdigest()}
        with tempfile.TemporaryDirectory() as directory,patch.dict(MODELS,{'tucano':info}):
            root=Path(directory);dest=root/'tucano';dest.mkdir();path=dest/'fixture.gguf'
            path.write_bytes(bad)
            with patch('urllib.request.urlopen',return_value=io.BytesIO(bad)):
                with self.assertRaises(ValueError):install(root)
            self.assertEqual(path.read_bytes(),bad)
            self.assertFalse(list(root.glob('llm-staging-*')))
            with patch('urllib.request.urlopen',return_value=io.BytesIO(good)) as download:
                self.assertTrue(install(root)['verified'])
                self.assertIn(info['revision'],download.call_args.args[0])
            self.assertEqual(path.read_bytes(),good)
            self.assertEqual(json.loads((dest/'arandu-model.json').read_text())['sha256'],info['sha256'])


class Service(unittest.IsolatedAsyncioTestCase):
    async def test_real_http_refusal_before_weights_and_transport_boundary(self):
        from aiohttp.test_utils import TestClient,TestServer
        from arandu_llm.service import create_app
        with tempfile.TemporaryDirectory() as directory:
            client=TestClient(TestServer(create_app(Path(directory),runtime_settings={'contract_version':2})))
            await client.start_server();self.addAsyncCleanup(client.close)
            body={'action':'llm','text':'Não liga nenhuma luz','catalog':catalog(),'accept_contracts':[1,2]}
            response=await client.post('/infer',json=body)
            self.assertEqual(response.status,403)
            headers={'Host':'127.0.0.1:11558','X-Arandu-Lab':'1'}
            response=await client.post('/infer',json=body,headers=headers)
            result=await response.json()
            self.assertEqual(response.status,200,result)
            self.assertEqual(result['kind'],'clarification')
            self.assertEqual(result['plan']['operations'],[])
            self.assertEqual(result['generated_tokens'],0)
            status=await client.get('/status',headers=headers)
            models=(await status.json())['models']
            self.assertEqual({m['id'] for m in models},{'tucano','qwen3'})
            self.assertTrue(all(not m['installed'] and not m['loaded'] for m in models))

    async def test_sse_measures_real_first_content_and_rejects_truncation(self):
        from aiohttp import web
        from aiohttp.test_utils import TestServer
        from arandu_llm.structured import completion
        from arandu_llm.adapter import LlmOutputError
        finish='stop'
        async def endpoint(request):
            body=await request.json()
            self.assertFalse(body['cache_prompt']);self.assertTrue(body['stream'])
            response=web.StreamResponse(headers={'Content-Type':'text/event-stream'})
            await response.prepare(request)
            for item in [{'choices':[{'delta':{'content':'{}'},'finish_reason':None}]},
                {'choices':[{'delta':{},'finish_reason':finish}],'usage':{'completion_tokens':2,'prompt_tokens':4}}]:
                await response.write(('data: '+json.dumps(item)+'\n\n').encode())
            await response.write(b'data: [DONE]\n\n');await response.write_eof();return response
        app=web.Application();app.router.add_post('/v1/chat/completions',endpoint)
        server=TestServer(app);await server.start_server();self.addAsyncCleanup(server.close)
        text,metrics=await completion(SimpleNamespace(port=server.port),{})
        self.assertEqual(text,'{}');self.assertGreaterEqual(metrics['first_token_ms'],0)
        self.assertEqual(metrics['generated_tokens'],2)
        finish='length'
        with self.assertRaises(LlmOutputError):await completion(SimpleNamespace(port=server.port),{})


class ControlBoundary(unittest.IsolatedAsyncioTestCase):
    async def test_llm_routes_do_not_mutate_on_get_or_without_csrf(self):
        from aiohttp.test_utils import TestClient,TestServer
        from test_control import control
        from unittest.mock import AsyncMock
        with tempfile.TemporaryDirectory() as directory,patch('modules.ModuleManager.start',new=AsyncMock()) as start,patch.object(control.LogStore,'configure_logging'):
            app=control.create_app(Path(directory),local=True);app.on_startup.clear();app.on_cleanup.clear()
            client=TestClient(TestServer(app),headers={'Host':'127.0.0.1:8770'})
            await client.start_server()
            try:
                response=await client.post('/api/llm/install',json={'model':'qwen3'})
                self.assertEqual(response.status,403)
                response=await client.get('/api/llm/install')
                self.assertEqual(response.status,405)
                response=await client.get('/api/state');state=await response.json()
                headers={'X-Arandu-CSRF':state['csrf']}
                response=await client.post('/api/llm/apply',json={'model':'qwen3'},headers=headers)
                self.assertEqual(response.status,422)
                response=await client.post('/api/llm/install',json={'model':'../../elsewhere'},headers=headers)
                self.assertEqual(response.status,422)
                start.assert_not_awaited()
            finally:await client.close()

    async def test_native_ha_llm_transport_requires_internal_source_and_no_browser_origin(self):
        from aiohttp import web
        from aiohttp.test_utils import make_mocked_request
        from test_control import control
        with tempfile.TemporaryDirectory() as directory,patch.object(control.LogStore,'configure_logging'):
            app=control.create_app(Path(directory),local=False)
            async def handler(_):return web.json_response({'accepted':True})
            for peer,headers,allowed in [
                ('172.30.32.1',{'X-Arandu-Lab':'1'},True),
                ('172.30.32.1',{},False),
                ('172.30.32.1',{'X-Arandu-Lab':'1','Origin':'https://attacker.invalid'},False),
                ('203.0.113.1',{'X-Arandu-Lab':'1'},False)]:
                transport=SimpleNamespace(get_extra_info=lambda key,default=None,p=peer:(p,1234) if key=='peername' else default)
                request=make_mocked_request('POST','/api/llm/apply',headers=headers,transport=transport,app=app)
                if allowed:self.assertEqual((await app.middlewares[0](request,handler)).status,200)
                else:
                    with self.assertRaises(web.HTTPForbidden):await app.middlewares[0](request,handler)


@unittest.skipUnless(os.environ.get('ARANDU_DECISION_STACK_URL') and os.environ.get('ARANDU_NLU_ENDPOINT'), 'Opt-in packaged real models and Rust')
class RealStack(unittest.IsolatedAsyncioTestCase):
    from test_decision_v2 import RealStack as _Setup
    asyncSetUp = _Setup.asyncSetUp

    async def test_real_cascade_parameter_context_preserves_fast_path(self):
        fixtures.add(self.hass,'climate_fixture','climate.fixture','Ar',{'supported_features':385,'temperature':23,
            'current_temperature':24,'min_temp':16,'max_temp':30,'target_temp_step':1},area='area_quarto')
        self.hass.services.available.add(('climate','set_temperature'))
        base=os.environ['ARANDU_DECISION_STACK_URL']
        async with self.session.get(base+'/api/state') as response:state=await response.json()
        async def select(version):
            async with self.session.post(base+'/api/llm/apply',headers={'X-Arandu-CSRF':state['csrf']},
                json={'model':'tucano','confirm':True,'options':{'contract_version':version,'max_tokens':384}}) as response:
                self.assertEqual(response.status,200,await response.text())
        rows=[]
        try:
            for version in [1,2]:
                await select(version);self.runtime.sessions.clear();self.hass.services.calls.clear()
                if hasattr(self.runtime,'last_intelligence'):del self.runtime.last_intelligence
                result=await self.runtime.process(fixtures.input_(text='Deixa o ar em vinte e três graus',satellite_id='assist_satellite.fixture'))
                self.assertEqual(result.code,'success',result)
                self.assertEqual(self.hass.services.calls[-1]['data']['temperature'],23)
                self.assertFalse(getattr(self.runtime,'last_intelligence',{}).get('llm'))
                self.hass.states.get('climate.fixture').attributes['temperature']=24
                before=time.perf_counter()
                result=await self.runtime.process(fixtures.input_(text='Aumenta mais dois',satellite_id='assist_satellite.fixture'))
                rows.append({'contract':version,'relative_result':result.code,'relative_wall_ms':(time.perf_counter()-before)*1000,
                    'trace':self.runtime.last_intelligence,'simulated_calls':len(self.hass.services.calls),'physical_actions':0})
                if version==2:
                    self.assertEqual(result.code,'success',result)
                    self.assertEqual(self.hass.services.calls[-1]['data']['temperature'],26)
                    self.assertEqual(self.runtime.last_intelligence['llm']['contract_version'],2)
                else:
                    self.assertNotEqual(result.code,'success',result)
                    self.assertEqual(len(self.hass.services.calls),1)
        finally:await select(2)
        if os.environ.get('ARANDU_LLM_CASCADE_OUTPUT'):
            Path(os.environ['ARANDU_LLM_CASCADE_OUTPUT']).write_text(json.dumps({'representative':False,'rows':rows},indent=2)+'\n',encoding='utf-8')

    async def test_native_real_models_compare_and_typed_dry_run_without_actions(self):
        from custom_components.local_nlu.ai_lab import Lab
        entry=SimpleNamespace(entry_id='fixture',options=self.options,runtime_data=SimpleNamespace(_client=self.client))
        self.hass.config_entries=SimpleNamespace(async_entries=lambda _:[entry])
        core=SimpleNamespace(Context=lambda user_id:SimpleNamespace(user_id=user_id))
        with patch.dict(sys.modules,{'homeassistant.core':core}):
            lab=Lab(self.hass)
            result=await lab._handle(self.hass.auth.user,{'command':'llm_compare','test_area_id':'area_quarto',
                'examples':[{'text':'Coloca o ventilador em 50%','expected_plan':{'type':'automation','operations':[
                    {'action':'percentage','entity_id':'fan.ventilador','parameters':{'value':50}}]}}]},
                self.session,os.environ['ARANDU_DECISION_STACK_URL'])
            self.assertTrue(result['dry_run']);self.assertFalse(result['execution_allowed'])
            for model in ['tucano','qwen3']:
                self.assertEqual(result['metrics'][model]['correct_plan'],1,result)
            self.assertEqual(self.hass.services.calls,[]);self.assertEqual(lab.proposals,{})
            result=await lab._handle(self.hass.auth.user,{'command':'run','mode':'llm','text':'Coloca o ventilador em 50%',
                'test_area_id':'area_quarto','llm_model':'tucano','llm_contract':2},self.session,os.environ['ARANDU_DECISION_STACK_URL'])
            self.assertTrue(result['operations_validated'],result)
            self.assertEqual(result['action_json'][0]['parameters']['value'],50)
            self.assertEqual(self.hass.services.calls,[])
