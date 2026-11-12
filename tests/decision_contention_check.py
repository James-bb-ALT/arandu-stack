"""Opt-in Decision/LLM contention on one isolated stack; no HA actions."""
import asyncio
import json
import os
from pathlib import Path
import time
from aiohttp import ClientSession,ClientTimeout
from decision_v2_check import catalog,percentiles


async def main():
    base=os.environ.get('ARANDU_DECISION_STACK_URL','http://127.0.0.1:18731')
    async with ClientSession(timeout=ClientTimeout(total=310)) as session:
        async with session.get(base+'/api/state') as r:state=await r.json()
        headers={'X-Arandu-CSRF':state['csrf'],'X-Arandu-Lab':'1'}
        async def post(path,data):
            async with session.post(base+path,json=data,headers=headers) as response:
                value=await response.json();assert response.status==200,(response.status,value);return value
        await post('/api/modules/llm/start',{})
        for _ in range(100):
            async with session.get(base+'/api/llm/models') as response:ready=await response.json()
            if ready.get('contract_version')==2:break
            await asyncio.sleep(.1)
        else:raise AssertionError('LLM not ready')
        await post('/api/llm/apply',{'model':'tucano','confirm':True,'options':{'max_tokens':384}})
        await post('/api/decision/apply',{'model':'e5-small','backend':'cpu','confirm':True})
        async def decision():return await post('/infer',{'action':'decision','text':'leva o ventilador','catalog':catalog(),'context':{'origin_area':'sala'}})
        async def llm():return await post('/infer',{'action':'llm','text':'Conta uma piada curta','catalog':catalog(),'accept_contracts':[1,2]})
        await decision();await llm();alone=[];rows=[]
        for _ in range(3):
            before=time.perf_counter();await decision();alone.append((time.perf_counter()-before)*1000)
        for _ in range(3):
            before=time.perf_counter();pending=asyncio.create_task(llm())
            for _ in range(100):
                async with session.get(base+'/api/llm/models') as response:status=await response.json()
                if status.get('busy'):break
                await asyncio.sleep(.01)
            queued=time.perf_counter();d=await decision();decision_wall=(time.perf_counter()-queued)*1000;l=await pending
            assert d['confidence'] is None and not l['proposal']['target_entities']
            rows.append({'decision_wall_ms':decision_wall,'decision_queue_ms':d['queue_ms'],
                'llm_inference_ms':l['inference_ms'],'llm_tokens':l['generated_tokens'],
                'mixed_wall_ms':(time.perf_counter()-before)*1000,'physical_actions':0})
        output={'representative':False,'warmup':1,'repetitions':3,'decision_model':'e5-small','llm_model':'tucano',
            'decision_alone_cached':percentiles(alone),'decision_contended_cached':percentiles([r['decision_wall_ms'] for r in rows]),
            'mixed_requests_per_second':6/(sum(r['mixed_wall_ms'] for r in rows)/1000),'rows':rows,
            'stt_contention':'not_run; no isolated STT model/audio reference provisioned','gpu_contention':'not_run; no Intel GPU exposed',
            'source_overrides':False,'physical_actions':0}
        Path(os.environ.get('ARANDU_CONTENTION_OUTPUT','.local-data/phase31/contention.json')).write_text(json.dumps(output,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(output),flush=True)


if __name__=='__main__':asyncio.run(main())
