"""Reproducible HA-simulated orchestration benchmark; never a model/audio benchmark."""
import asyncio
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(sys.argv[2]).resolve() if len(sys.argv)>2 else Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'arandu_stack/rootfs/modules/arandu_nlu/rootfs/src/tests/mlp')]
from test_contextual import Client, hass_, input_, operation, plan
from custom_components.local_nlu.contextual_runtime import ContextualRuntime


async def run():
    import psutil
    process = psutil.Process()
    cpu_start = sum(process.cpu_times()[:2])
    samples = []
    done = asyncio.Event()
    async def sample():
        while not done.is_set():
            samples.append(process.memory_info().rss/1048576)
            await asyncio.sleep(.005)
    sampler=asyncio.create_task(sample())
    client = Client(plan(operation()))
    original = client.async_interpret_v4
    async def delayed(payload):
        await asyncio.sleep(.03)
        return await original(payload)
    client.async_interpret_v4 = delayed
    runtime = ContextualRuntime(hass_(), client)
    latencies = []
    started = time.perf_counter()
    async def request(i):
        start = time.perf_counter()
        result = await runtime.process(input_(conversation_id=f'bench-{i}'))
        latencies.append((time.perf_counter()-start)*1000)
        return result.code
    try:
        results = []
        for batch in range(5):
            results.extend(await asyncio.gather(*(request(batch*8+i) for i in range(8))))
        elapsed = time.perf_counter()-started
        ordered = sorted(latencies)
        percentile = lambda p: ordered[min(len(ordered)-1, round((len(ordered)-1)*p))]
        return dict(kind='HA_SIMULATED', model_inference='not_run', audio='not_observed',
                    nlu_delay_ms=30, requests=len(results), concurrency=8, batches=5,
                    p50_ms=statistics.median(latencies), p95_ms=percentile(.95), p99_ms=percentile(.99),
                    throughput_rps=len(results)/elapsed, successes=results.count('success'),
                    cpu_process_ms=(sum(process.cpu_times()[:2])-cpu_start)*1000,
                    rss_max_sampled_mb=max(samples) if samples else None,
                    rss_mean_sampled_mb=statistics.mean(samples) if samples else None,
                    memory_sampling_ms=5, physical_ha=False,
                    effects=len(runtime.hass.services.calls), unexpected_effects=0 if len(runtime.hass.services.calls)==40 else None)
    finally:
        done.set()
        await sampler
        runtime.close()


if __name__ == '__main__':
    result = asyncio.run(run())
    print(json.dumps(result, indent=2))
    if len(sys.argv)>1:
        Path(sys.argv[1]).write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
