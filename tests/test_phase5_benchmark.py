"""Opt-in development benchmark; technical fixtures, no linguistic metrics."""
import asyncio
import json
import os
from pathlib import Path
import statistics
import sys
import time


async def run():
    # Import only when explicitly executed: do not collect the fixture TestCase twice.
    from test_phase5_runtime import Phase5
    fixture=Phase5('test_automation_enable_is_not_trigger_and_conditions_are_explicit')
    await fixture.asyncSetUp()
    client=fixture.runtime.client
    timings=[]
    original=client.async_interpret_v4
    async def measured(payload):
        start=time.perf_counter()
        result=await original(payload)
        timings.append((time.perf_counter()-start)*1000)
        return result
    client.async_interpret_v4=measured
    query_times=[]
    original_query=fixture.runtime.query
    async def measured_query(*args,**kwargs):
        start=time.perf_counter()
        result=await original_query(*args,**kwargs)
        query_times.append((time.perf_counter()-start)*1000)
        return result
    fixture.runtime.query=measured_query
    totals=[]
    try:
        for _ in range(100):
            start=time.perf_counter()
            result=await fixture.say('Qual cômodo está mais quente?')
            assert result.code=='query_success',result
            totals.append((time.perf_counter()-start)*1000)
        assert not fixture.hass.services.calls
        def report(values):
            return {'p50_ms':statistics.median(values),'p95_ms':sorted(values)[94]}
        return {'kind':'REAL_RUST_HTTP_SIMULATED_HA','samples':100,
                'nlu_http':report(timings),'ha_read_render':report(query_times),'ha_query_end_to_end':report(totals),
                'physical_actions':0,'neural_calls':0,
                'scope':'Development Windows host; not an i5-6500T measurement. HA timing includes catalog, transport and rendering.'}
    finally:
        fixture.patch.stop()
        await fixture.runtime.async_close()
        await fixture.http.close()


if __name__=='__main__':
    if not os.environ.get('ARANDU_NLU_ENDPOINT'):raise SystemExit('ARANDU_NLU_ENDPOINT required')
    result=asyncio.run(run())
    Path(sys.argv[1]).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
