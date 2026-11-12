"""Measure frozen HTTP v1 evaluation without changing semantic outcomes."""
from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from arandu_adapter import AdapterError, interpret, start_local
from corpus import load_fixtures
from freeze import MANIFEST, sha256_file, verify_manifest
from semantic import compare_case, load_json

ROOT = Path(__file__).resolve().parent
REPOSITORY = ROOT.parent.parent
DEFAULT_OUTPUT = REPOSITORY / "target" / "ptbr-independent"
WARMUP_ROUNDS = 3
MEASURED_ROUNDS = 7
COLD_SAMPLES = 3


def percentile(values: list[int], percentage: float) -> int:
    if not values:
        raise ValueError("cannot calculate percentile of an empty sample")
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * percentage)))
    return ordered[index]


def statistics_ns(values: list[int]) -> dict[str, int | float]:
    return {
        "count": len(values),
        "min_ns": min(values),
        "max_ns": max(values),
        "mean_ns": statistics.fmean(values),
        "p50_ns": percentile(values, 0.50),
        "p95_ns": percentile(values, 0.95),
        "p99_ns": percentile(values, 0.99),
    }


def exercise(endpoint: str, product_catalog: Any, cases: list[dict[str, Any]], semantic_catalog: Any) -> tuple[list[int], list[dict[str, str]]]:
    durations: list[int] = []
    failures: list[dict[str, str]] = []
    for case in cases:
        started = time.monotonic_ns()
        try:
            actual = load_json(json.dumps(interpret(endpoint, case["raw"]["text"], product_catalog)))
            comparison = compare_case(case["expected"], actual, semantic_catalog)
        except AdapterError as error:
            failures.append({"id": case["id"], "classification": "protocol_error", "detail": str(error)})
            continue
        durations.append(time.monotonic_ns() - started)
        if comparison.classification != "exact_pass":
            failures.append({"id": case["id"], "classification": comparison.classification, "detail": comparison.detail})
    return durations, failures


def cold_sample(binary: Path, product_catalog: Any, case: dict[str, Any], semantic_catalog: Any) -> dict[str, Any]:
    started = time.monotonic_ns()
    server = start_local(binary)
    ready = time.monotonic_ns()
    try:
        actual = load_json(json.dumps(interpret(server.endpoint, case["raw"]["text"], product_catalog)))
        finished = time.monotonic_ns()
        comparison = compare_case(case["expected"], actual, semantic_catalog)
        return {
            "start_to_ready_ns": ready - started,
            "start_to_first_request_ns": finished - started,
            "classification": comparison.classification,
            "detail": comparison.detail,
        }
    finally:
        server.close()


def render_markdown(report: dict[str, Any]) -> str:
    steady = report["http_steady_state"]
    request = steady["request_latency"]
    rounds = steady["round_duration"]
    summary = report["summary"]
    lines = [
        "# Benchmark HTTP PT-BR independente",
        "",
        f"- Freeze: `{report['freeze_id']}`",
        f"- Casos por rodada: {steady['requests_per_round']}",
        f"- Warm-ups descartados: {steady['warmup_rounds_discarded']}",
        f"- Rodadas medidas: {steady['measured_rounds']}",
        f"- Falhas steady-state: {summary['failures']}",
        f"- Falhas cold start: {summary['cold_failures']}",
        f"- RSS: {summary['rss']}",
        "",
        "## Latência por request",
        "",
        "| Métrica | Nanosegundos |",
        "| --- | ---: |",
    ]
    for name in ("min_ns", "p50_ns", "p95_ns", "p99_ns", "max_ns", "mean_ns"):
        lines.append(f"| `{name}` | {request[name]} |")
    lines.extend([
        "",
        "## Duração por rodada",
        "",
        "| Métrica | Nanosegundos |",
        "| --- | ---: |",
    ])
    for name in ("min_ns", "p50_ns", "p95_ns", "p99_ns", "max_ns", "mean_ns"):
        lines.append(f"| `{name}` | {rounds[name]} |")
    lines.extend([
        "",
        f"- Throughput: {steady['throughput_requests_per_second']} requests/s",
        f"- Amostras cold start: {len(report['cold_start']['samples'])}",
        "",
    ])
    return "\n".join(lines)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True, help="local release binary to start")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    binary = arguments.binary.resolve()
    if not binary.is_file():
        raise SystemExit(f"release binary is missing: {binary}")
    if not MANIFEST.exists():
        raise SystemExit(f"freeze manifest does not exist: {MANIFEST}")
    verify_manifest(json.loads(MANIFEST.read_text(encoding="utf-8")))
    semantic_catalog, cases = load_fixtures(ROOT)
    product_catalog = load_json((ROOT / "data" / "catalog-v1.json").read_text(encoding="utf-8"))
    server = start_local(binary)
    try:
        for _ in range(WARMUP_ROUNDS):
            _, failures = exercise(server.endpoint, product_catalog, cases, semantic_catalog)
            if failures:
                raise SystemExit(f"warm-up has {len(failures)} semantic or protocol failures")
        round_durations: list[int] = []
        request_durations: list[int] = []
        failures: list[dict[str, str]] = []
        for _ in range(MEASURED_ROUNDS):
            started = time.monotonic_ns()
            durations, round_failures = exercise(server.endpoint, product_catalog, cases, semantic_catalog)
            round_durations.append(time.monotonic_ns() - started)
            request_durations.extend(durations)
            failures.extend(round_failures)
    finally:
        server.close()
    cold = [cold_sample(binary, product_catalog, cases[0], semantic_catalog) for _ in range(COLD_SAMPLES)]
    cold_failures = [sample for sample in cold if sample["classification"] != "exact_pass"]
    report = {
        "schema_version": 1,
        "freeze_id": "ptbr-independent-v1",
        "environment": {"python": sys.version.split()[0], "platform": platform.platform()},
        "hashes": {
            "adapter": sha256_file(ROOT / "arandu_adapter.py"),
            "comparison": sha256_file(ROOT / "semantic.py"),
            "benchmark": sha256_file(ROOT / "benchmark.py"),
            "binary": sha256_file(binary),
        },
        "http_steady_state": {
            "warmup_rounds_discarded": WARMUP_ROUNDS,
            "measured_rounds": MEASURED_ROUNDS,
            "requests_per_round": len(cases),
            "request_latency": statistics_ns(request_durations),
            "round_duration": statistics_ns(round_durations),
            "throughput_requests_per_second": len(request_durations) / (sum(round_durations) / 1_000_000_000),
            "failures": failures,
        },
        "cold_start": {"samples": cold},
        "summary": {
            "cases": len(cases),
            "failures": len(failures),
            "cold_failures": len(cold_failures),
            "rss": "not collected: stdlib-only runner has no portable Windows RSS API",
        },
    }
    output_dir = arguments.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "benchmark-v1.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output_dir / "benchmark-v1.md").write_text(render_markdown(report), encoding="utf-8")
    print(f"ptbr-independent benchmark wrote {len(request_durations)} measurements to {output_dir}")
    if failures or cold_failures:
        raise SystemExit("ptbr-independent benchmark observed semantic or protocol failures")


if __name__ == "__main__":
    main()
