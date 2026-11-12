"""Real Rust/LLM with simulated HA delivery; no physical effects or playback."""
import asyncio
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'arandu_stack/rootfs/modules/arandu_nlu/rootfs/src/tests/mlp')]
from test_contextual import hass_, input_
from phase4_scenarios import Http
from custom_components.local_nlu.contextual_runtime import ContextualRuntime
from custom_components.local_nlu.request_trace import capture
from aiohttp import ClientSession, ClientTimeout


async def main():
    output = {'kind':'REAL_RUST_REAL_LLM_SIMULATED_HA','physical_actions':0,'physical_playback':'not_observed'}
    async with ClientSession(timeout=ClientTimeout(total=60)) as session:
        hass = hass_()
        hass.async_create_task = asyncio.create_task
        options = {'stack_endpoint':os.environ['ARANDU_DECISION_STACK_URL'],'decision_enabled':True,'llm_enabled':True}
        runtime = ContextualRuntime(hass,Http(session),lambda:options)
        runtime.delivery_handler.async_deliver_notification = AsyncMock(return_value=True)
        helpers = SimpleNamespace(async_get_clientsession=lambda _:session)
        async def process(value):
            with capture(hass,runtime,options,value) as trace:
                result=await runtime.process(value)
                trace.finish(result)
                return result
        try:
            with patch.dict(sys.modules,{'homeassistant.helpers.aiohttp_client':helpers}):
                started = time.perf_counter()
                joke = asyncio.create_task(process(input_('Conta uma piada curta',conversation_id='joke')))
                async with asyncio.timeout(10):
                    while not runtime.model_admission.active and not joke.done():
                        await asyncio.sleep(.005)
                assert runtime.model_admission.active == 1, 'LLM did not enter the model queue'
                timer_started = time.perf_counter()
                timers = await asyncio.gather(*(
                    process(input_(text,conversation_id=name)) for name,text in (
                        ('pizza','Timer para pizza de 15 minutos'),('bolo','Timer para bolo de 10 minutos'))))
                assert all(result.code=='success' for result in timers), timers
                output['two_timers_ms'] = (time.perf_counter()-timer_started)*1000
                output['llm_pending_after_timers'] = not joke.done()
                jobs = runtime.scheduler.list_jobs('timer')
                assert sorted(job.name for job in jobs) == ['bolo','pizza']
                for kind in ('alarm','reminder'):
                    jobs.append(await runtime.scheduler.async_add_job(job_type=kind,target_action=kind,
                        delay_seconds=10,title='Fixture técnica'))
                for job in jobs:
                    job.due_timestamp = time.time()
                await runtime.scheduler._async_handle_due_jobs()
                await asyncio.gather(*tuple(runtime.scheduler._tasks))
                output['deliveries_while_llm_pending'] = not joke.done()
                output['simulated_deliveries'] = runtime.delivery_handler.async_deliver_notification.await_count
                assert output['simulated_deliveries'] == 4
                sleep = await process(input_('Desliga o ventilador daqui a 15 minutos',conversation_id='sleep'))
                assert sleep.code=='success', sleep
                cancelled = await runtime.scheduler.async_cancel_jobs(job_type='action',all_of_type=True)
                assert len(cancelled)==1
                await runtime.scheduler._async_handle_due_jobs()
                result = await joke
                assert result.code=='query_success' and result.reason=='llm_conversation', result
                assert result.response_text and not hass.services.calls
                output.update(joke_code=result.code,joke_reply=result.response_text,
                    llm_total_ms=(time.perf_counter()-started)*1000,sleep_cancelled=True,
                    unexpected_simulated_effects=len(hass.services.calls))
                await asyncio.sleep(.2)
        finally:
            await runtime.async_close()
            if getattr(runtime,'_trace_publisher',None):
                runtime._trace_publisher.close()
    path = Path(os.environ['ARANDU_LIVE_OUTPUT'])
    path.write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(output,ensure_ascii=False))


if __name__=='__main__':
    asyncio.run(main())
