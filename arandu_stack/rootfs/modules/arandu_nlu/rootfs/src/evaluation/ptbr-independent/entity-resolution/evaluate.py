"""Independent post-implementation evaluation for entity resolution.

Gates (all must pass, no gold label is ever rewritten here):
1. preparation freeze v4 verifies, oracle validates, generator reproduces;
2. Rust entity-resolution tests pass (`--test entity_resolution` plus lib);
3. release benchmark probe matches all gold labels and reports timing.

Then records `baselines/post-implementation-v1.json` (refuses to overwrite).
Peak process memory is sampled externally via Win32 `GetProcessMemoryInfo`
while the probe runs; the probe itself never reports memory.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "evaluation" / "ptbr-independent" / "entity-resolution"
DESTINATION = BASE / "baselines" / "post-implementation-v1.json"
TOOLCHAIN = Path(r"C:\Users\victo\.rustup\toolchains\1.98.0-x86_64-pc-windows-msvc\bin")
CARGO = str(TOOLCHAIN / "cargo.exe")
ITERATIONS = 20_000


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, capture_output=True, text=True, **kwargs)
    if completed.returncode != 0:
        raise SystemExit(f"command failed: {' '.join(command)}\n{completed.stderr[-2000:]}")
    return completed


def peak_working_set(process: subprocess.Popen[str], poll_s: float = 0.02) -> int | None:
    """Sample Win32 PeakWorkingSetSize externally until the child exits."""
    try:
        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        handle = kernel32.OpenProcess(0x0400 | 0x0010, False, process.pid)
        if not handle:
            return None

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_uint32),
                ("PageFaultCount", ctypes.c_uint32),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        def sample() -> int:
            counters = Counters()
            counters.cb = ctypes.sizeof(Counters)
            if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                return counters.PeakWorkingSetSize
            return 0

        peak = 0
        try:
            while process.poll() is None:
                peak = max(peak, sample())
                time.sleep(poll_s)
            peak = max(peak, sample())
        finally:
            kernel32.CloseHandle(handle)
        return peak or None
    except (OSError, AttributeError, ValueError):
        return None


def main() -> None:
    if DESTINATION.exists():
        raise SystemExit(f"refusing to overwrite post-implementation baseline: {DESTINATION}")
    run([sys.executable, str(BASE / "freeze.py"), "--check"])
    run([sys.executable, str(BASE / "generate.py"), "--check"])
    oracle = run([sys.executable, str(BASE / "oracle.py")])
    print(oracle.stdout.strip())
    run([CARGO, "test", "--workspace", "--locked", "--offline", "--lib"], cwd=ROOT)
    run([CARGO, "test", "--workspace", "--locked", "--offline", "--test", "entity_resolution"], cwd=ROOT)
    run([CARGO, "build", "--workspace", "--locked", "--offline", "--release", "--examples"], cwd=ROOT)

    probe = ROOT / "target" / "release" / "examples" / "er_bench.exe"
    launched = subprocess.Popen(
        [str(probe), "--iterations", str(ITERATIONS)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    peak = peak_working_set(launched)
    stdout, stderr = launched.communicate()
    if launched.returncode != 0:
        raise SystemExit(f"benchmark probe failed: {stderr[-2000:]}")
    timing = json.loads(stdout)
    assert timing["cases"] == 21 and timing["gold_mismatches"] == 0

    rows = [json.loads(line) for line in (BASE / "corpus-v1.jsonl").read_text(encoding="utf-8").splitlines() if line]
    outcomes: dict[str, int] = {}
    for row in rows:
        key = row["expected"]["outcome"]
        outcomes[key] = outcomes.get(key, 0) + 1
    files = ["spec-v1.json", "catalog-v1.json", "corpus-v1.jsonl", "generate.py", "oracle.py", "freeze.py", "evaluate.py"]
    value = {
        "schema_version": 1,
        "baseline_id": "ptbr-independent-entity-resolution-post-implementation-v1",
        "status": "implemented_tested_baselined",
        "active": False,
        "activation_note": "resolver is additive library API; protocol v1 interpret() path and Home Assistant integration are unchanged and unwired",
        "product_output_used": False,
        "product_changes": [
            "addon/engine/src/resolution.rs",
            "addon/engine/src/lib.rs",
            "addon/engine/src/model.rs",
            "addon/engine/tests/entity_resolution.rs",
            "addon/engine/examples/er_bench.rs",
            "evaluation/ptbr-independent/entity-resolution/evaluate.py",
        ],
        "conformance": {
            "cases": len(rows),
            "gold_outcomes": {key: outcomes.get(key, 0) for key in ("ambiguous", "no_match", "resolved")},
            "gold_mismatches": 0,
            "rust_unit_tests": "cargo test --workspace --locked --offline --lib: pass",
            "rust_gold_tests": "cargo test --workspace --locked --offline --test entity_resolution (21 gold rows + catalog permutation): pass",
        },
        "benchmark": {
            "probe": "target/release/examples/er_bench.exe",
            "iterations": timing["iterations"],
            "mean_ns_per_case": timing["mean_ns_per_case"],
            "throughput_cases_per_second": timing["throughput_cases_per_second"],
            "peak_working_set_bytes": peak,
            "memory_collection": "Win32 GetProcessMemoryInfo PeakWorkingSetSize sampled externally during probe run",
        },
        "inputs": {name: {"path": f"evaluation/ptbr-independent/entity-resolution/{name}", "sha256": digest(BASE / name)} for name in files},
        "freeze_manifest": {"path": "evaluation/ptbr-independent/entity-resolution/freeze-v4.json", "sha256": digest(BASE / "freeze-v4.json")},
    }
    DESTINATION.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(f"post-implementation baseline created: {DESTINATION}")


if __name__ == "__main__":
    main()
