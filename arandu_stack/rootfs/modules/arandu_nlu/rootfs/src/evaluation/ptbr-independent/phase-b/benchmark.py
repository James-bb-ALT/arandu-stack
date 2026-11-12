"""Benchmark independent Phase B corpus through local HTTP v1."""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "evaluation" / "ptbr-independent"))
from arandu_adapter import AdapterError, interpret, start_local  # noqa: E402
from common import CATALOG_PATH, CORPUS_PATH  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--binary", type=Path)
    source.add_argument("--endpoint")
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--output", type=Path, default=ROOT / "target" / "ptbr-independent-phase-b" / "benchmark-v1.json")
    args = parser.parse_args()
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    cases = [json.loads(line) for line in CORPUS_PATH.read_text(encoding="utf-8").splitlines() if line]
    cold_start_started = time.perf_counter()
    server = start_local(args.binary.resolve()) if args.binary is not None else None
    cold_start_ms = (time.perf_counter() - cold_start_started) * 1000 if server is not None else None
    endpoint = server.endpoint if server is not None else args.endpoint
    first_request_started = time.perf_counter()
    first_request_failures = 0
    try:
        try:
            interpret(endpoint, cases[0]["text"], catalog)
        except AdapterError:
            first_request_failures = 1
        first_request_ms = (time.perf_counter() - first_request_started) * 1000
        for _ in range(args.warmup):
            for row in cases:
                interpret(endpoint, row["text"], catalog)
        measurements: list[float] = []
        failures = 0
        round_durations: list[float] = []
        for _ in range(args.rounds):
            started = time.perf_counter()
            for row in cases:
                request_started = time.perf_counter()
                try:
                    interpret(endpoint, row["text"], catalog)
                except AdapterError:
                    failures += 1
                measurements.append((time.perf_counter() - request_started) * 1000)
            round_durations.append(time.perf_counter() - started)
        measurements.sort()
        def percentile(value: float) -> float:
            index = min(len(measurements) - 1, int((len(measurements) - 1) * value))
            return measurements[index]
        total = sum(round_durations)
        report = {
            "schema_version": 1,
            "freeze_id": "ptbr-independent-phase-b-v1",
            "requests": len(measurements),
            "failures": failures,
            "warmup_rounds": args.warmup,
            "measured_rounds": args.rounds,
            "cold_start_ms": cold_start_ms,
            "first_request_ms": first_request_ms,
            "first_request_failures": first_request_failures,
            "latency_ms": {"min": measurements[0], "mean": statistics.fmean(measurements), "p50": percentile(.50), "p95": percentile(.95), "p99": percentile(.99), "max": measurements[-1]},
            "throughput_requests_per_second": len(measurements) / total,
            "round_duration_seconds": round_durations,
            "memory": server.memory_bytes() if server is not None else {},
        }
    finally:
        if server is not None:
            server.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
