"""Integration tests for FIX A: Real LLM RAM residency, telemetry, and control plane."""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "arandu_stack/app"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "arandu_stack/rootfs/modules/arandu_llm/rootfs/src/src"))

from arandu_llm import service as llm_service
from arandu_llm import structured as llm_structured
from arandu_stack.app.llm_lab import LLMModelManager
from arandu_stack.app.model_memory import ResidencyPolicy
from arandu_stack.app.modules import DEFAULTS, ModuleManager, validate_options
from arandu_stack.app.settings import FIELDS


class TestResidencyConfigAndManager(unittest.IsolatedAsyncioTestCase):
    def test_defaults_settings_and_validation(self):
        self.assertEqual(DEFAULTS["llm"]["resident_policy"], "LAZY")
        field_keys = [f["id"] for f in FIELDS["llm"]]
        self.assertIn("resident_policy", field_keys)
        policy_field = next(f for f in FIELDS["llm"] if f["id"] == "resident_policy")
        self.assertEqual(policy_field["type"], "select")
        self.assertEqual([c[0] for c in policy_field["choices"]], ["RESIDENT", "LAZY", "OFF"])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for p in ("RESIDENT", "LAZY", "OFF"):
                opts = validate_options("llm", {"resident_policy": p}, root)
                self.assertEqual(opts["resident_policy"], p)
            with self.assertRaises(ValueError):
                validate_options("llm", {"resident_policy": "ALWAYS_ON"}, root)

    async def test_llm_lab_preload_without_callback_never_fakes_residency(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = LLMModelManager(data_dir=Path(tmp), upstream_port=11570)
            try:
                mgr.set_residency_policy("tucano", ResidencyPolicy.RESIDENT)
                with patch("arandu_stack.app.llm_lab.is_installed", return_value=True):
                    result = await mgr.preload_resident_models(loader_callback=None)
                self.assertEqual(result, [])
                self.assertNotIn("tucano", mgr.memory.loaded_models)
            finally:
                await mgr.close()

    async def test_llm_lab_preload_with_callback_registers_only_on_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = LLMModelManager(data_dir=Path(tmp), upstream_port=11570, total_memory_budget_mb=4096)
            try:
                mgr.set_residency_policy("lfm2.5-1.2b", ResidencyPolicy.LAZY)
                mgr.set_residency_policy("tucano", ResidencyPolicy.RESIDENT)
                cb = AsyncMock(return_value=True)
                with patch("arandu_stack.app.llm_lab.is_installed", return_value=True):
                    result = await mgr.preload_resident_models(loader_callback=cb)
                self.assertEqual(result, ["tucano"])
                self.assertIn("tucano", mgr.memory.loaded_models)
                cb.assert_awaited_once_with("tucano")
            finally:
                await mgr.close()


class _FakeAdapter:
    def __init__(self, settings, should_fail=False):
        self.settings = {
            "context_size": 2048,
            "max_tokens": 256,
            "temperature": 0.1,
            **settings,
        }
        self.threads = 2
        self.should_fail = should_fail
        self.ensure_calls = []
        self.active_model = None
        self.process = None
        self.verified_models = set()
        self._model_identity = None

    async def unload(self):
        self.active_model = None
        self.process = None

    async def close(self):
        await self.unload()


class TestLlmServiceResidency(unittest.IsolatedAsyncioTestCase):
    def _make_fake_app(self, policy="LAZY", available_mb=4096.0, should_fail=False):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        fake_bin = root / "llama-server"
        fake_bin.write_bytes(b"fake")
        fake_adapter = _FakeAdapter(
            settings={"llm_model": "tucano", "resident_policy": policy},
            should_fail=should_fail,
        )

        async def fake_ensure(adapter, model_id, **kwargs):
            adapter.ensure_calls.append(model_id)
            if adapter.should_fail:
                raise RuntimeError("boom_load")
            adapter.active_model = model_id
            adapter.process = SimpleNamespace(pid=43210, returncode=None)
            adapter._model_identity = (model_id, "abc", 1024)
            adapter.verified_models.add((model_id, "abc", 1024))
            return 12.5

        with patch.object(llm_service, "Llama", return_value=fake_adapter), \
             patch.object(llm_service, "installed", return_value=True), \
             patch.object(llm_service, "_available_memory_mb", return_value=available_mb), \
             patch.object(llm_service, "_process_rss_mb", return_value=640.0):
            app = llm_service.create_app(
                root,
                executable=str(fake_bin),
                threads=2,
                runtime_settings={"llm_model": "tucano", "resident_policy": policy},
            )
            return tmp, app, fake_adapter, fake_ensure

    async def test_resident_startup_preloads_and_reuses_runner(self):
        tmp, app, fake_adapter, fake_ensure = self._make_fake_app(policy="RESIDENT", available_mb=4096.0)
        try:
            with patch.object(llm_structured, "ensure", side_effect=fake_ensure), \
                 patch.object(llm_service, "_available_memory_mb", return_value=4096.0), \
                 patch.object(llm_service, "_process_rss_mb", return_value=640.0), \
                 patch.object(llm_service, "installed", return_value=True):
                server = TestServer(app)
                client = TestClient(server)
                await client.start_server()
                try:
                    await asyncio.sleep(0.05)
                    hdrs = {"Host": f"127.0.0.1:{llm_service.PORT}", "X-Arandu-Lab": "1"}
                    resp = await client.get("/status", headers=hdrs)
                    self.assertEqual(resp.status, 200)
                    st = await resp.json()
                    self.assertEqual(st["resident_policy"], "RESIDENT")
                    self.assertTrue(st["is_model_loaded"])
                    self.assertTrue(st["model_ready"])
                    self.assertEqual(st["load_state"], "ready")
                    self.assertTrue(st["preload_attempted"])
                    self.assertTrue(st["preload_success"])
                    self.assertIsNotNone(st["loaded_since"])
                    self.assertEqual(len(fake_adapter.ensure_calls), 1)
                finally:
                    await client.close()
        finally:
            tmp.cleanup()

    async def test_resident_memory_pressure_reports_honest_failure(self):
        tmp, app, fake_adapter, fake_ensure = self._make_fake_app(policy="RESIDENT", available_mb=200.0)
        try:
            with patch.object(llm_structured, "ensure", side_effect=fake_ensure), \
                 patch.object(llm_service, "_available_memory_mb", return_value=200.0), \
                 patch.object(llm_service, "_process_rss_mb", return_value=120.0), \
                 patch.object(llm_service, "installed", return_value=True):
                server = TestServer(app)
                client = TestClient(server)
                await client.start_server()
                try:
                    await asyncio.sleep(0.05)
                    hdrs = {"Host": f"127.0.0.1:{llm_service.PORT}", "X-Arandu-Lab": "1"}
                    resp = await client.get("/status", headers=hdrs)
                    st = await resp.json()
                    self.assertEqual(st["resident_policy"], "RESIDENT")
                    self.assertFalse(st["is_model_loaded"])
                    self.assertFalse(st["model_ready"])
                    self.assertEqual(st["load_state"], "degraded")
                    self.assertEqual(st["load_error_code"], "memory_pressure")
                    self.assertTrue(st["preload_attempted"])
                    self.assertFalse(st["preload_success"])
                    self.assertEqual(fake_adapter.ensure_calls, [])
                finally:
                    await client.close()
        finally:
            tmp.cleanup()

    async def test_lazy_does_not_preload_and_off_rejects_infer(self):
        tmp, app, fake_adapter, fake_ensure = self._make_fake_app(policy="LAZY", available_mb=4096.0)
        try:
            with patch.object(llm_structured, "ensure", side_effect=fake_ensure), \
                 patch.object(llm_service, "_available_memory_mb", return_value=4096.0), \
                 patch.object(llm_service, "_process_rss_mb", return_value=120.0), \
                 patch.object(llm_service, "installed", return_value=True):
                server = TestServer(app)
                client = TestClient(server)
                await client.start_server()
                try:
                    await asyncio.sleep(0.05)
                    hdrs = {"Host": f"127.0.0.1:{llm_service.PORT}", "X-Arandu-Lab": "1"}
                    st = await (await client.get("/status", headers=hdrs)).json()
                    self.assertEqual(st["resident_policy"], "LAZY")
                    self.assertFalse(st["is_model_loaded"])
                    self.assertEqual(st["load_state"], "unloaded")
                    self.assertFalse(st["preload_attempted"])

                    # Switch to OFF via /infer apply
                    apply_off = await client.post("/infer", headers=hdrs, json={"action": "apply", "llm_model": "tucano", "resident_policy": "OFF"})
                    self.assertEqual(apply_off.status, 200)
                    off_body = await apply_off.json()
                    self.assertFalse(off_body["loaded"])
                    self.assertEqual(off_body["load_state"], "off")

                    # Inference under OFF must fail explicitly with 422 code=llm_disabled_by_residency
                    infer_resp = await client.post("/infer", headers=hdrs, json={"text": "oi", "catalog": {"entities": [], "areas": []}})
                    self.assertEqual(infer_resp.status, 422)
                    err_body = await infer_resp.json()
                    self.assertEqual(err_body["code"], "llm_disabled_by_residency")
                finally:
                    await client.close()
        finally:
            tmp.cleanup()


class TestDashboardAndControlPlaneUi(unittest.TestCase):
    def test_app_js_exposes_honest_residency_telemetry_and_apply_prompt(self):
        app_js = (Path(__file__).resolve().parents[1] / "arandu_stack/app/static/app.js").read_text(encoding="utf-8")
        self.assertIn("Política: '+policy", app_js)
        self.assertIn("Em RAM: '+inRam", app_js)
        self.assertIn("options.resident_policy", app_js)
        self.assertIn("api('llm/apply'", app_js)


if __name__ == "__main__":
    unittest.main()
