#!/usr/bin/env python3
"""Real Rust HTTP and Python executor timing with explicitly simulated HA."""
import argparse
import asyncio
import json
import math
import platform
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests/mlp")]
from test_contextual import EndToEndTests, HttpClient, add, hass_, input_
from custom_components.local_nlu.contextual_runtime import ContextualRuntime
from custom_components.local_nlu.contextual_catalog import build


def percentiles(values):
    values = sorted(values)
    return {"p50_ms": values[math.ceil((len(values) - 1) * .5)], "p95_ms": values[math.ceil((len(values) - 1) * .95)], "p99_ms": values[math.ceil((len(values) - 1) * .99)]}


class MeasuredClient(HttpClient):
    async def async_interpret_v4(self, payload):
        start = time.perf_counter()
        result = await super().async_interpret_v4(payload)
        self.roundtrip_ms = (time.perf_counter() - start) * 1000
        return result


async def measure(endpoint):
    reports = []
    for size in (64, 512, 4096):
        hass = hass_()
        hass.entity_registry.entities.clear()
        hass.states._states.clear()
        hass.exposed.clear()
        for i in range(size):
            add(hass, f"fixture_{i}", f"light.fixture_{i}", f"Fixture luz {i}", {"supported_color_modes": ["brightness"], "brightness": 128}, "on")
        options = {"default_area": "area_sala"}
        compilation = []
        for _ in range(5):
            start = time.perf_counter()
            snapshot = build(hass, hass.auth.user, options)
            compilation.append((time.perf_counter() - start) * 1000)
        client = MeasuredClient(endpoint)
        start = time.perf_counter()
        await client.async_catalog_v4(snapshot.payload)
        registration = (time.perf_counter() - start) * 1000
        runtime = ContextualRuntime(hass, client, lambda: options)
        phases = {}
        for n in range(220):
            text = "Liga Fixture luz 0" if n % 2 else "Qual é o estado de Fixture luz 0?"
            start = time.perf_counter()
            result = await runtime.process(input_(text, conversation_id=f"benchmark_{n}"))
            elapsed = (time.perf_counter() - start) * 1000
            if result.code not in ("success", "query_success"):
                raise RuntimeError((size, text, result))
            if n >= 20:
                for key, value in {**result.timings, "rust_http_roundtrip_ms": client.roundtrip_ms, "validation_executor_response_ms": result.timings["validation_ms"] + result.timings["ha_execution_response_ms"], "observed_rust_http_executor_ms": elapsed}.items():
                    phases.setdefault(key, []).append(value)
        reports.append({"entities": size, "iterations": 200, "warmup": 20, "python_snapshot_compile": percentiles(compilation), "rust_catalog_http_register_ms": registration, "snapshot_bytes": len(json.dumps(snapshot.payload).encode()), "phases": {key: percentiles(values) for key, values in phases.items()}})
    return {"kind": "Rust HTTP + actual Python executor, simulated HA instant service; not residential HA latency", "environment": {"platform": platform.platform(), "python": platform.python_version(), "cpuinfo": Path('/proc/cpuinfo').read_text().split('model name')[1].split('\n')[0].strip() if Path('/proc/cpuinfo').exists() else platform.processor()}, "catalogs": reports}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    EndToEndTests.setUpClass()
    try:
        report = asyncio.run(measure(EndToEndTests.endpoint))
    finally:
        EndToEndTests.tearDownClass()
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({item['entities']: item['phases']['observed_rust_http_executor_ms'] for item in report['catalogs']}))


if __name__ == '__main__':
    main()
