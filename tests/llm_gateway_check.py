"""Opt-in final-image LLM control, rollback and inference smoke; no HA execution."""
import asyncio
import json
import os
from pathlib import Path
from aiohttp import ClientSession,ClientTimeout
from llm_v2_check import catalog


async def main():
    base=os.environ.get('ARANDU_LLM_STACK_URL','http://127.0.0.1:8770')
    async with ClientSession(timeout=ClientTimeout(total=190),headers={'Host':'127.0.0.1:8770'}) as session:
        async with session.get(base+'/api/state') as response:state=await response.json()
        headers={'X-Arandu-CSRF':state['csrf'],'X-Arandu-Lab':'1'}
        async def request(path,body=None,ok=True):
            async with session.request('POST' if body is not None else 'GET',base+path,json=body,headers=headers) as response:
                value=await response.json()
                if ok:assert response.status==200,(response.status,value)
                return response.status,value
        async def apply(model,tokens=384):
            return await request('/api/llm/apply',{'model':model,'confirm':True,'options':{'max_tokens':tokens}},ok=False)
        async def infer(**extra):
            return await request('/infer',{'action':'llm','text':'Coloca a luz da sala em 40%','catalog':catalog(),
                'context':{'origin_area':'sala'},'accept_contracts':[1,2],**extra},ok=False)
        report={'app_version':state['version'],'physical_ha':False,'actions':0,'source_overrides':False}
        status,value=await apply('tucano');assert status==200,value
        _,value=await request('/api/llm/models')
        assert value['active_model']=='tucano' and value['configured_model']=='tucano',value
        assert next(m for m in value['models'] if m['id']=='tucano')['integrity_verified']
        report['tucano_verified']=True
        installed={m['id'] for m in value['models'] if m['installed']}
        status,value=await apply('qwen3')
        if 'qwen3' not in installed:
            assert status!=200,value
            _,value=await request('/api/llm/models')
            assert value['active_model']==value['configured_model']=='tucano',value
            report['missing_model_rollback']=True
        else:
            assert status==200,value
            _,value=await request('/api/llm/models')
            assert value['active_model']==value['configured_model']=='qwen3',value
            status,value=await infer();assert status==200 and value['plan']['operations'][0]['parameters']['value']==40,value
            report['qwen3_verified_and_inferred']=True
        status,value=await apply('tucano');assert status==200,value
        status,first=await infer();assert status==200,first
        status,warm=await infer();assert status==200 and warm['load_ms']==0,warm
        assert first['plan']['operations'][0]['parameters']['value']==40
        report['resident_reuse']=True
        results=await asyncio.gather(*(infer() for _ in range(6)))
        assert sum(status==200 for status,_ in results)==1,results
        report['concurrency_statuses']=[status for status,_ in results]
        status,value=await apply('tucano',64);assert status==200,value
        status,value=await infer();assert status!=200 and 'truncat' in json.dumps(value).lower(),value
        report['truncation_rejected']=True
        status,value=await apply('tucano');assert status==200,value
        # Old clients retain the original contract; new clients receive typed v2.
        status,value=await infer(accept_contracts=[1]);assert status==200 and value.get('contract_version',1)==1,value
        report['legacy_negotiation']=True
        _,value=await request('/api/llm/models')
        assert value['configured_model']==value['active_model']=='tucano',value
        report['final_model']='tucano'
        Path(os.environ.get('ARANDU_LLM_GATEWAY_OUTPUT','.local-data/phase3/gateway.json')).write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(report))


if __name__=='__main__':asyncio.run(main())
