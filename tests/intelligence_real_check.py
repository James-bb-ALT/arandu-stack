"""Opt-in real E5/Tucano smoke. Requires installed weights; never downloads."""
import asyncio
import json
import os
from pathlib import Path
import time

from arandu_decision.adapter import Encoder
from arandu_llm.adapter import Llama

CATALOG={'areas':[{'area_id':'sala','names':['Sala']}],'entities':[
    {'entity_id':'fan.fixture','name':'Ventilador','aliases':[], 'domain':'fan','area_id':'sala',
     'actions':['turn_on','turn_off','query'],'attributes':{'available':True}}]}

async def main():
    root=Path(os.environ['ARANDU_REAL_MODELS'])
    encoder=Encoder(root/'decision',2)
    decisions=[]
    for text in ['liga ventilador','acnde ventilador','faz o ventilador funcionar','faz o ventilador funcionar']:
        result=encoder.infer(text,CATALOG,'e5-small',{},context={'origin_area':'sala'})
        decisions.append({'text':text,**result})
    assert decisions[0]['decision_status']=='resolved' and not decisions[0]['embedding_used']
    assert decisions[1]['decision_status']=='confirmation_required' and decisions[1]['confidence'] is None
    assert decisions[-1]['load_ms']==0
    encoder.unload()
    llama=Llama(root/'llm','/usr/local/bin/llama-server',2,settings={'max_tokens':256})
    replies=[]
    try:
        for text in ['Conta uma piada curta','Qual é a capital da França?']:
            started=time.perf_counter()
            result=await llama.infer({'text':text},CATALOG)
            assert result['proposal']['intent']=='conversation',result
            assert result['response_text'] and not result['proposal']['target_entities']
            replies.append({'text':text,**result,'request_total_ms':(time.perf_counter()-started)*1000})
        assert replies[1]['load_ms']==0
    finally:
        await llama.unload()
    output={'hardware':'AMD Ryzen 7 5700X, Docker Linux; not target i5-6500T',
            'threads':2,'model_weights_real':True,'decision':decisions,'llm':replies,
            'home_assistant_actions':0,'physical_ha_validated':False}
    Path(os.environ.get('ARANDU_REAL_OUTPUT',str(root/'real-result.json'))).write_text(json.dumps(output,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print('PASS: real E5, deterministic/confirmation paths, warm cache, two real LLM replies, resident reuse, no HA execution')

if __name__=='__main__':asyncio.run(main())
