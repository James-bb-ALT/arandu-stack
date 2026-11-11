"""Opt-in isolated stack only. Real model selection/inference/rollback; no HA actions."""
import asyncio
import json
import os
from pathlib import Path
from aiohttp import ClientSession,ClientTimeout,ClientError
from decision_v2_check import catalog


async def main():
    base=os.environ.get('ARANDU_DECISION_STACK_URL','http://127.0.0.1:18731')
    report={'physical_actions':0,'source_overrides':os.environ.get('ARANDU_SOURCE_OVERRIDES')=='1','models':{}}
    async with ClientSession(timeout=ClientTimeout(total=310)) as session:
        for _ in range(100):
            try:
                async with session.get(base+'/api/decision/models') as r:ready=await r.json()
                if len(ready.get('models',[]))==3:break
            except (OSError,ValueError,ClientError,TimeoutError):pass
            await asyncio.sleep(.2)
        else:raise AssertionError('Decision not ready')
        async with session.get(base+'/api/state') as r:state=await r.json()
        headers={'X-Arandu-CSRF':state['csrf'],'X-Arandu-Lab':'1'}
        async def post(path,data,ok=True):
            async with session.post(base+path,json=data,headers=headers) as response:
                value=await response.json()
                if ok:assert response.status==200,(response.status,value)
                return response.status,value
        _,value=await post('/api/decision/install',{'model':'minilm-multilingual','backend':'cpu'})
        assert value['installed'] and value['verified'];report['explicit_http_install_verify']=True
        for key in ['e5-small','minilm-multilingual','e5-base']:
            _,value=await post('/api/decision/apply',{'model':key,'backend':'cpu','confirm':True})
            assert value['loaded'] and value['integrity_verified'] and value['backend_used']=='cpu'
            _,value=await post('/infer',{'action':'decision','text':'leva o ventilador','catalog':catalog(),'context':{'origin_area':'sala'}})
            assert value['model']==key and value['embedding_used'] and value['backend_used']=='cpu',value
            assert value['dimension']==(768 if key=='e5-base' else 384)
            assert value['ranking_ms']>=0 and value['embedding_ms']>=0 and value['tokenization_ms']>=0
            assert value['confidence'] is None and value['decision_status']!='accepted',value
            report['models'][key]={'passed':True,'dimension':value['dimension'],'calibrated_acceptance':False}
        await post('/api/decision/apply',{'model':'e5-small','backend':'cpu','confirm':True})
        policy={'algorithm':'baseline-v2','threshold':.93,'min_margin':.14}
        _,value=await post('/api/decision/apply',{'model':'e5-small','backend':'cpu','confirm':True,'options':policy})
        assert value['applied_config']==policy,value
        _,value=await post('/infer',{'action':'decision','text':'leva o ventilador','catalog':catalog(),'context':{'origin_area':'sala'}})
        assert all(value['applied_config'][k]==v for k,v in policy.items()),value
        report['live_algorithm_threshold_margin_applied']=True
        await post('/api/decision/apply',{'model':'e5-small','backend':'cpu','confirm':True,
            'options':{'algorithm':'structured-v3','threshold':.8,'min_margin':.08}})
        status,value=await post('/api/decision/apply',{'model':'e5-base','backend':'gpu_intel','confirm':True},False)
        assert status!=200 and value.get('code')=='backend_unavailable',value
        async with session.get(base+'/api/decision/models') as r:value=await r.json()
        assert value['configured_model']==value['active_model']=='e5-small' and value['configured_backend']==value['backend_used']=='cpu',value
        report['gpu_failure_rollback']=True;report['gpu']=next(b for b in value['backends'] if b['id']=='gpu_intel')
        async with session.post(base+'/api/decision/apply',json={'model':'e5-small','backend':'cpu','confirm':True}) as response:assert response.status==403
        async with session.get(base+'/api/decision/apply') as response:assert response.status==405
        status,_=await post('/api/decision/install',{'model':'../../escape'},False);assert status==422
        status,_=await post('/api/modules/decision/options',{'options':{'model':'e5-base'}},False);assert status==422
        # Fresh uncached catalog makes overlapping requests meaningful; exactly one job is admitted.
        data=catalog()
        data['entities'] += [{**data['entities'][0],'entity_id':f'fan.fixture_{i}','name':f'Fixture {i}'} for i in range(128)]
        body={'action':'decision','text':'leva o ventilador','catalog':data,'context':{'origin_area':'sala'}}
        pending=asyncio.create_task(post('/infer',body,False));await asyncio.sleep(.01)
        status,_=await post('/api/decision/apply',{'model':'e5-base','backend':'cpu','confirm':True},False)
        assert status!=200,'Model switch must not run concurrently with inference'
        assert (await pending)[0]==200
        report['switch_during_inference_rejected']=True
        report['transport_and_csrf']=True
        Path(os.environ.get('ARANDU_DECISION_GATEWAY_OUTPUT','.local-data/phase31/gateway.json')).write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(report),flush=True)


if __name__=='__main__':asyncio.run(main())
