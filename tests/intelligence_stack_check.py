"""Opt-in API/real-model test against an isolated local validation stack."""
import asyncio
import json
from pathlib import Path
import time

from aiohttp import ClientSession,ClientTimeout,ClientError

CATALOG={'areas':[{'area_id':'sala','names':['Sala']}],'entities':[
    {'entity_id':'fan.fixture','name':'Ventilador','aliases':[], 'domain':'fan','area_id':'sala',
     'actions':['turn_on','turn_off','query'],'attributes':{'available':True}}]}

async def main():
    base='http://127.0.0.1:8770'
    async with ClientSession(timeout=ClientTimeout(total=90)) as session:
        for _ in range(100):
            try:
                async with session.get(base+'/status') as response:ready=await response.json()
                if all(row['available'] for row in ready['models']):break
            except (ClientError,TimeoutError,ValueError):pass
            await asyncio.sleep(.5)
        else:raise AssertionError('services not ready')
        async with session.get(base+'/api/state') as response:state=await response.json()
        assert state['version']=='5.0.0'
        assert next(row for row in state['modules'] if row['id']=='stt')['options']['stt_model']=='orukeet_int8'
        headers={'X-Arandu-CSRF':state['csrf'],'X-Arandu-Lab':'1'}
        async def infer(action,text):
            start=time.perf_counter()
            async with session.post(base+'/infer',json={'action':action,'text':text,'catalog':CATALOG},headers=headers) as response:
                result=await response.json();assert response.status==200,result
            result['http_total_ms']=(time.perf_counter()-start)*1000
            return result
        explicit=await infer('decision','liga ventilador')
        assert explicit['decision_status']=='resolved' and explicit['confidence'] is None
        typo=await infer('decision','acnde ventilador')
        assert typo['decision_status']=='confirmation_required' and typo['confirmation_required']
        semantic=await infer('decision','faz o ventilador funcionar')
        assert semantic['load_ms']==0 and semantic['confidence'] is None
        joke=await infer('llm','Conta uma piada curta')
        assert joke['proposal']['intent']=='conversation' and joke['response_text']
        answer=await infer('llm','Qual é a capital da França?')
        assert 'paris' in answer['response_text'].casefold() and answer['load_ms']==0
        definition=await infer('llm','O que é uma lâmpada?')
        assert definition['proposal']['intent']=='conversation' and definition['response_text']
        for row in [joke,answer,definition]:assert not row['proposal']['target_entities'] and not row['confirmation_required']
        # The public control transport still rejects unauthenticated browser mutations.
        async with session.post(base+'/infer',json={'action':'llm','text':'oi','catalog':CATALOG}) as response:
            assert response.status==403
        result={'app_version':state['version'],'stt_default':'orukeet_int8','explicit':explicit,'typo':typo,
                'semantic_preloaded':semantic,'joke':joke,'answer':answer,'definition':definition,'internal_transport_enforced':True,
                'home_assistant_actions':0,'physical_ha_validated':False,'source_overrides':False}
        Path('.local-data/intelligence-validation/stack-result.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
        print('PASS: final image API, real E5 preloaded, grounded Decision, confirmation, real joke/general answer, resident LLM, Orukeet default, protected transport')

if __name__=='__main__':asyncio.run(main())
