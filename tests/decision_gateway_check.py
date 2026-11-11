"""Opt-in isolated Docker stack only. Save/restart and real concurrent E5 requests."""
import asyncio
from collections import Counter
import json
import os
from pathlib import Path
import time

from aiohttp import ClientSession, ClientTimeout

BASE = 'http://127.0.0.1:8770'
CATALOG = {'areas':[{'area_id':'sala','names':['Sala']}], 'entities':[
    {'entity_id':f'light.fixture_{i}','name':f'Luminária fixture {i}','domain':'light',
     'aliases':[], 'area_id':'sala','actions':['turn_on','turn_off','query'],
     'attributes':{'available':True}} for i in range(256)]}


async def main():
    assert os.environ.get('ARANDU_DECISION_VALIDATE_MUTATIONS')=='1', 'Opt in for isolated container configuration changes'
    async with ClientSession(timeout=ClientTimeout(total=185)) as session:
        async with session.get(BASE+'/api/state') as response:
            state=await response.json()
        assert state['version']=='7.0.0'
        headers={'X-Arandu-CSRF':state['csrf'],'X-Arandu-Lab':'1'}
        options=next(r for r in state['modules'] if r['id']=='decision')['options']
        async def post(path, body):
            async with session.post(BASE+path,json=body,headers=headers) as response:
                return response.status, await response.json()
        async def select(algorithm):
            status,body=await post('/api/modules/decision/options',{'options':{**options,'algorithm':algorithm}})
            assert status==200,body
            status,body=await post('/api/modules/decision/restart',{})
            assert status==200,body
            for _ in range(100):
                status,body=await post('/infer',{'action':'readiness','catalog':CATALOG})
                if status==200:
                    assert body.get('algorithm','baseline-v2')==algorithm,body
                    return body
                await asyncio.sleep(.2)
            raise AssertionError('Decision did not become ready')
        selected=[]
        try:
            for algorithm in ('baseline-v2','structured-v3'):
                readiness=await select(algorithm)
                status,result=await post('/infer',{'action':'decision','text':'Liga a luminária fixture 255',
                    'catalog':CATALOG,'context':{'origin_area':'sala'}})
                assert status==200,result
                # Baseline replies predate an explicit algorithm field.
                assert result.get('algorithm','baseline-v2')==algorithm,result
                assert result['proposal']['target_entities']==['light.fixture_255'],result
                selected.append({'algorithm':algorithm,'readiness':readiness,'decision_status':result['decision_status']})
            begin=time.perf_counter()
            responses=await asyncio.gather(*(post('/infer',{'action':'decision',
                'text':'Faça a luminária fixture funcionar','catalog':CATALOG,'compare_algorithms':True}) for _ in range(8)))
            counts=Counter()
            for status,result in responses:
                counts[str(status)]+=1
                if status==200:
                    assert result['dry_run'] is True and result['proposal'] is None
                else:
                    assert status==502 and result['code']=='upstream_http_429',result
            assert counts['200'] and counts['502'],counts
            output={'local_container_only':True,'selection_save_restart':selected,
                'concurrent_requests':8,'http_statuses':dict(counts),'busy_upstream_status':429,
                'wall_ms':(time.perf_counter()-begin)*1000,'home_assistant_actions':0}
            Path('.local-data/phase2/gateway-result.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            print('PASS: algorithm selection/save/restart, real concurrent E5 with bounded busy rejection; zero HA effects')
        finally:
            await select(options['algorithm'])


if __name__=='__main__':asyncio.run(main())
