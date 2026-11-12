"""Tests for Decision RAM Residency (RESIDENT / LAZY / OFF) and real readiness telemetry."""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys

import aiohttp
from aiohttp.test_utils import TestClient, TestServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "arandu_stack/rootfs/modules/arandu_decision/rootfs/src/src"))

from arandu_decision.service import create_app


def _run_client_test(policy: str, fn):
    async def runner():
        tmp = ROOT / ".pytest_cache" / f"decision_residency_{policy.lower()}"
        tmp.mkdir(parents=True, exist_ok=True)
        app = create_app(tmp, runtime_settings={"resident_policy": policy})
        async with TestClient(TestServer(app)) as client:
            return await fn(client)

    return asyncio.run(runner())


def test_decision_residency_off_never_loads_weights_and_runs_partial_phonetics():
    async def check(client):
        headers = {"X-Arandu-Lab": "1", "Host": "127.0.0.1:11557"}
        resp = await client.get("/status", headers=headers)
        assert resp.status == 200
        status_data = await resp.json()
        assert status_data["resident_policy"] == "OFF"
        assert status_data["is_model_loaded"] is False
        assert status_data["cache_status"] == "disabled"
        assert isinstance(status_data["pid"], int)

        partial_req = {
            "action": "partial",
            "partial_understanding": {
                "version": 5,
                "request_id": "res-1",
                "catalog_generation": "a" * 64,
                "identity_scope_hash": "b" * 64,
                "origin": {"area_id": "quarto", "source": "satellite"},
                "request_kind": "possible_device_action",
                "clauses": [
                    {
                        "clause_id": 0,
                        "original_text": "lingua a tv",
                        "slots": {
                            "action": {"value": None, "status": "unknown", "evidence": []},
                            "target": {"value": "tv_quarto", "status": "resolved", "evidence": ["authorized_exact_alias:tv"]},
                        },
                        "allowed_actions": ["turn_on", "turn_off"],
                        "constraints": {"negated": False, "excluded_targets": [], "conditional": False, "quoted_content": False},
                        "unresolved": ["action"],
                    }
                ],
                "safety_flags": [],
            },
        }
        infer_resp = await client.post("/infer", json=partial_req, headers=headers)
        assert infer_resp.status == 200
        data = await infer_resp.json()
        assert data["status"] == "resolved_candidate"
        assert data["repairs"][0]["value"] == "turn_on"
        assert data["is_model_loaded"] is False
        assert data["backend_used"] == "deterministic_phonetics"

    _run_client_test("OFF", check)


def test_decision_residency_lazy_and_resident_report_honest_state_without_weights():
    async def check_lazy(client):
        headers = {"X-Arandu-Lab": "1", "Host": "127.0.0.1:11557"}
        resp = await client.get("/status", headers=headers)
        data = await resp.json()
        assert data["resident_policy"] == "LAZY"
        assert data["is_model_loaded"] is False
        assert data["cache_status"] == "cold"

        switch_resp = await client.post("/infer", json={"action": "residency", "resident_policy": "RESIDENT"}, headers=headers)
        assert switch_resp.status == 200
        sw = await switch_resp.json()
        assert sw["resident_policy"] == "RESIDENT"
        assert sw["is_model_loaded"] is False  # Honest: no weights installed in temp dir

    _run_client_test("LAZY", check_lazy)
