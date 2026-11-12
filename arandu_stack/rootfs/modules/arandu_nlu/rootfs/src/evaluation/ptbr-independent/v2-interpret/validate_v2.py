"""Independent post-implementation evaluation for v2 interpretation.

Gates (all must pass, no gold label is ever rewritten here):
1. preparation freeze v1 verifies, oracle validates, generator reproduces;
2. Rust v2 tests pass (lib units + 24-row gold corpus + permutation);
3. the v2 snapshot catalog subschema is identical to the entity-resolution
   request catalog subschema (single contract, two files);
4. all 24 gold rows replay over POST /v2/interpret with request/response
   schema validation in both directions and zero gold mismatches, timed
   end-to-end (this is the full-flow benchmark, not the resolver probe).

Then records `baselines/post-implementation-v1.json` (refuses to overwrite).
Peak process memory is sampled externally via Win32 GetProcessMemoryInfo
while a sustained HTTP pass runs.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "evaluation" / "ptbr-independent" / "v2-interpret"
DESTINATION = BASE / "baselines" / "post-implementation-v1.json"
CARGO = str(Path(r"C:\Users\victo\.rustup\toolchains\1.98.0-x86_64-pc-windows-msvc\bin") / "cargo.exe")
REQUEST_SCHEMA = json.loads((ROOT / "schemas" / "v2-interpret-request.schema.json").read_text(encoding="utf-8"))
RESPONSE_SCHEMA = json.loads((ROOT / "schemas" / "v2-interpret-response.schema.json").read_text(encoding="utf-8"))
ER_REQUEST_SCHEMA = json.loads(
    (ROOT / "schemas" / "entity-resolution-request.schema.json").read_text(encoding="utf-8")
)

try:
    import jsonschema

    def check(instance: object, schema: dict) -> None:
        jsonschema.validate(instance=instance, schema=schema)

except ImportError:  # pragma: no cover
    raise SystemExit("validate_v2 requires the jsonschema package")

sys.path.insert(0, str(ROOT / "evaluation" / "ptbr-independent"))
from arandu_adapter import start_local  # noqa: E402


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, capture_output=True, text=True, **kwargs)
    if completed.returncode != 0:
        raise SystemExit(f"command failed: {' '.join(command)}\n{completed.stderr[-2000:]}")
    return completed


def peak_working_set(pid: int, samples: int = 20, poll_s: float = 0.05) -> int | None:
    """Sample Win32 PeakWorkingSetSize a fixed number of times (never loops)."""
    try:
        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        handle = kernel32.OpenProcess(0x0400 | 0x0010, False, pid)
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
            for _ in range(samples):
                peak = max(peak, sample())
                time.sleep(poll_s)
        finally:
            kernel32.CloseHandle(handle)
        return peak or None
    except (OSError, AttributeError, ValueError):
        return None


def post(endpoint: str, body: bytes) -> tuple[int, bytes]:
    request = urllib.request.Request(
        f"{endpoint}/v2/interpret",
        data=body,
        headers={"Content-Type": "application/json", "Host": "localhost"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def expected(row: dict) -> tuple[int, dict]:
    want = row["expected"]
    if want["status"] == "plan":
        return 200, {"status": "plan", "version": 2, "operations": want["operations"]}
    return 200, {"status": want["status"], "version": 2}


def main() -> None:
    if DESTINATION.exists():
        raise SystemExit(f"refusing to overwrite post-implementation baseline: {DESTINATION}")

    if REQUEST_SCHEMA["properties"]["snapshot"] != ER_REQUEST_SCHEMA["properties"]["catalog"]:
        raise SystemExit("snapshot subschema drift between v2-interpret and entity-resolution schemas")
    print("snapshot subschema identical across contracts")

    run([sys.executable, str(BASE / "freeze.py"), "--check"])
    run([sys.executable, str(BASE / "generate.py"), "--check"])
    oracle = run([sys.executable, str(BASE / "oracle.py")])
    print(oracle.stdout.strip())
    run([CARGO, "test", "--workspace", "--locked", "--offline", "--lib"], cwd=ROOT)
    run([CARGO, "test", "--workspace", "--locked", "--offline", "--test", "v2_interpret"], cwd=ROOT)
    run([CARGO, "build", "--workspace", "--locked", "--offline", "--release"], cwd=ROOT)

    snapshot = json.loads((BASE / "snapshot-v1.json").read_text(encoding="utf-8"))
    rows = [
        json.loads(line)
        for line in (BASE / "corpus-v1.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    payloads = [
        json.dumps(
            {"text": row["text"], "snapshot": snapshot, "generation": row["generation"]}
        ).encode("utf-8")
        for row in rows
    ]
    for payload in payloads:
        check(json.loads(payload), REQUEST_SCHEMA)

    binary = ROOT / "target" / "release" / "local-nlu.exe"
    server = start_local(binary.resolve())
    mismatches = []
    try:
        for row, payload in zip(rows, payloads):
            status, raw = post(server.endpoint, payload)
            actual = json.loads(raw)
            check(actual, RESPONSE_SCHEMA)
            want_status, want_body = expected(row)
            if status != want_status or actual != want_body:
                mismatches.append({"id": row["id"], "status": status, "actual": actual})
        ROUNDS = 40
        all_payloads = payloads * ROUNDS
        for payload in payloads:
            post(server.endpoint, payload)
        started = time.perf_counter_ns()
        for payload in all_payloads:
            post(server.endpoint, payload)
        total_ns = time.perf_counter_ns() - started
        mean_ns = total_ns / len(all_payloads)
    finally:
        server.close()

    hold = subprocess.Popen(
        [str(binary), "serve", "--listen", "127.0.0.1:11999"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True,
    )
    time.sleep(1.0)
    try:
        endpoint = "http://127.0.0.1:11999"
        deadline = time.perf_counter_ns() + 8_000_000_000
        while time.perf_counter_ns() < deadline:
            for payload in payloads:
                post(endpoint, payload)
        peak = peak_working_set(hold.pid)
    finally:
        hold.terminate()

    if mismatches:
        raise SystemExit(f"HTTP conformance FAILED: {len(mismatches)} mismatches: {mismatches[:3]}")

    outcomes: dict[str, int] = {}
    for row in rows:
        key = row["expected"]["status"]
        outcomes[key] = outcomes.get(key, 0) + 1
    files = ["snapshot-v1.json", "corpus-v1.jsonl", "generate.py", "oracle.py", "freeze.py", "validate_v2.py"]
    schemas = ["v2-interpret-request.schema.json", "v2-interpret-response.schema.json"]
    value = {
        "schema_version": 1,
        "baseline_id": "ptbr-independent-v2-interpret-post-implementation-v1",
        "status": "implemented_tested_baselined",
        "active": False,
        "activation_note": "v2 path is additive and local-only; protocol v1 and Home Assistant execution are unchanged and unwired",
        "product_output_used": False,
        "product_changes": [
            "addon/engine/src/v2.rs",
            "addon/engine/src/extraction.rs",
            "addon/engine/src/parser.rs",
            "addon/engine/src/resolution.rs",
            "addon/engine/src/normalize.rs",
            "addon/engine/src/model.rs",
            "addon/engine/src/server.rs",
            "addon/engine/src/lib.rs",
            "addon/engine/tests/v2_interpret.rs",
            "addon/engine/tests/v2_interpret_http.rs",
            "addon/engine/examples/mx_shadow.rs",
            "custom_components/local_nlu/client.py",
            "custom_components/local_nlu/protocol.py",
            "schemas/v2-interpret-request.schema.json",
            "schemas/v2-interpret-response.schema.json",
            "evaluation/ptbr-independent/v2-interpret/validate_v2.py",
        ],
        "conformance": {
            "cases": len(rows),
            "gold_outcomes": {key: outcomes.get(key, 0) for key in ("ambiguous", "no_match", "plan")},
            "gold_mismatches": 0,
            "schema_bidirectional": "request/response schemas validate all live traffic",
            "rust_unit_tests": "cargo test --workspace --locked --offline --lib: pass",
            "rust_gold_tests": "cargo test --workspace --locked --offline --test v2_interpret (24 gold rows + snapshot permutation): pass",
        },
        "benchmark_end_to_end": {
            "endpoint": "POST /v2/interpret over loopback HTTP",
            "cases_per_round": len(rows),
            "rounds": ROUNDS,
            "mean_ns_per_case": mean_ns,
            "throughput_cases_per_second": 1_000_000_000 / mean_ns,
            "peak_working_set_bytes": peak,
            "memory_collection": "Win32 GetProcessMemoryInfo PeakWorkingSetSize sampled externally during sustained traffic",
        },
        "inputs": {name: {"path": f"evaluation/ptbr-independent/v2-interpret/{name}", "sha256": digest(BASE / name)} for name in files},
        "schemas": {name: {"path": f"schemas/{name}", "sha256": digest(ROOT / "schemas" / name)} for name in schemas},
        "freeze_manifest": {"path": "evaluation/ptbr-independent/v2-interpret/freeze-v1.json", "sha256": digest(BASE / "freeze-v1.json")},
    }
    DESTINATION.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(f"post-implementation baseline created: {DESTINATION}")


if __name__ == "__main__":
    main()
