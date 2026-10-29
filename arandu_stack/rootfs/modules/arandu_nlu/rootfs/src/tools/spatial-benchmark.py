#!/usr/bin/env python3
"""FIXTURE_TECNICA: bounded spatial plans, real Rust HTTP, simulated HA only."""
import asyncio
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests/mlp")]
from test_contextual import EndToEndTests, HttpClient, add, input_
from test_contextual_spatial import house
from custom_components.local_nlu.contextual_runtime import ContextualRuntime
from custom_components.local_nlu.contextual_catalog import build


def percentiles(values):
    values = sorted(values)
    return {"p50_ms": values[(len(values) - 1) // 2], "p95_ms": values[-1], "samples": len(values)}


async def measure():
    reports = []
    for size in (64, 512, 4096):
        hass = house()
        for n in range(29):
            add(hass, f"extra_{n:04}", f"light.extra_{n}", f"Luminária técnica {n}", {})
        for n in range(size - len(build(hass, hass.auth.user, {}).payload["entities"])):
            add(hass, f"other_{n:04}", f"light.other_{n}", f"Outra luminária {n}", {}, area="area_cozinha")
        runtime = ContextualRuntime(hass, HttpClient(EndToEndTests.endpoint))
        phases = {}
        for n in range(10):
            for text, count in (("liga a sala", 32), ("desliga a sala", 32), ("luz", 31)):
                hass.services.calls.clear()
                start = time.perf_counter()
                result = await runtime.process(input_(text, conversation_id=f"bench_{n}_{count}_{text[:1]}", satellite_id="assist_satellite.sala"))
                elapsed = (time.perf_counter() - start) * 1000
                if result.code != "success" or len(hass.services.calls) != count:
                    raise RuntimeError((size, text, result, len(hass.services.calls)))
                if n >= 2:
                    for key, value in {**result.timings, "observed_ms": elapsed}.items():
                        phases.setdefault(text, {}).setdefault(key, []).append(value)
        reports.append({"catalog_entities": size, "phases": {text: {key: percentiles(values) for key, values in timings.items()} for text, timings in phases.items()}})
    return {"kind": "Rust HTTP + Python executor; simulated HA, no residential services; p95 is sample maximum for 8 samples", "catalogs": reports}


if __name__ == "__main__":
    if not os.environ.get("ARANDU_NLU_BINARY"):
        raise SystemExit("Set ARANDU_NLU_BINARY to the tested release binary")
    EndToEndTests.setUpClass()
    try:
        report = asyncio.run(measure())
    finally:
        EndToEndTests.tearDownClass()
    destination = Path(sys.argv[1])
    destination.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({row["catalog_entities"]: {text: phases["observed_ms"] for text, phases in row["phases"].items()} for row in report["catalogs"]}))
