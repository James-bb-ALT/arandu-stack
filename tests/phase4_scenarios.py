"""Real Rust HTTP + simulated HA; temporal/exception execution, never real HA."""
import asyncio
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(sys.argv[2]).resolve() if len(sys.argv)>2 else Path(__file__).parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'arandu_stack/rootfs/modules/arandu_nlu/rootfs/src/tests/mlp')]
from test_contextual import hass_,input_,add
from custom_components.local_nlu.contextual_runtime import ContextualRuntime
from aiohttp import ClientSession,ClientTimeout


class Http:
    def __init__(self,session):self.session=session
    async def post(self,path,payload):
        async with self.session.post(os.environ['ARANDU_NLU_ENDPOINT']+path,json=payload,allow_redirects=False) as response:
            assert response.status==200,response.status
            return await response.json()
    async def async_catalog_v4(self,payload):return await self.post('/v4/catalog',payload)
    async def async_interpret_v4(self,payload):return await self.post('/v4/interpret',payload)


async def run():
    cases=[('light','Liga a luz da sala','success'),('sensor','Qual a temperatura da sala','query_success'),
        ('schedule','Desliga o ventilador daqui a 15 minutos','success'),
        ('timer_pizza','Timer para pizza de 15 minutos','success'),('timer_bolo','Timer para bolo de 10 minutos','success'),
        ('alarm','Acorda às 7 da manhã','success'),('reminder','Me lembra de comprar pão às 15h','success'),
        ('exclusion','Desliga tudo da sala, menos a TV','success'),('negation','Não liga a luz da sala','cancelled')]
    output={'kind':'REAL_RUST_SIMULATED_HA','physical_actions':0,'model_inference':'disabled','cases':[]}
    async with ClientSession(timeout=ClientTimeout(total=5)) as session:
        hass=hass_();hass.async_create_task=asyncio.create_task
        add(hass,'tv_fixture','media_player.tv','TV',{'supported_features':512|256|16384},area='area_sala')
        runtime=ContextualRuntime(hass,Http(session))
        try:
            for name,text,expected in cases:
                samples=[]
                results=[]
                for rep in range(5):
                    start=time.perf_counter()
                    result=await runtime.process(input_(text,conversation_id=name+str(rep)))
                    samples.append((time.perf_counter()-start)*1000)
                    results.append({'code':result.code,'reason':result.reason})
                ordered=sorted(samples)
                output['cases'].append({'scenario':name,'expected':expected,'results':results,'p50_ms':ordered[2],'p95_ms':ordered[-1],'p99_ms':ordered[-1],
                    'pass':all(row['code']==expected for row in results)})
            output['excluded_tv_effects']=sum(call.get('target',{}).get('entity_id')==['media_player.tv'] for call in hass.services.calls)
            output['timer_names']=sorted({job.name for job in runtime.scheduler.list_jobs('timer')})
            output['success_scenarios']=sum(row['pass'] for row in output['cases'])
        finally:
            runtime.close()
    return output


if __name__=='__main__':
    result=asyncio.run(run())
    Path(sys.argv[1]).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
