"""FIXTURE_TECNICA: real local HTTP boundary; optional aiohttp environment."""
import importlib.util
from pathlib import Path
import unittest


@unittest.skipUnless(importlib.util.find_spec("aiohttp"), "optional local app aiohttp environment")
class LocalAppBoundary(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer
        spec = importlib.util.spec_from_file_location("arandu_local_test", Path(__file__).resolve().parents[2] / "tools/ai_lab/local_app.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.client = TestClient(TestServer(module.create_app()))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        self.headers = {"Host": "127.0.0.1:8766", "X-Arandu-Lab-UI": "1"}

    async def test_empty_catalog_is_disclosed(self):
        response = await self.client.get("/", headers=self.headers)
        self.assertEqual(response.status, 200)
        self.assertIn("Catálogo vazio", await response.text())

    async def test_execute_always_refused(self):
        response = await self.client.post("/api/lab", json={"command": "execute", "confirm": True}, headers=self.headers)
        self.assertEqual(response.status, 422)

    async def test_foreign_origins_and_hosts_rejected(self):
        response = await self.client.post("/api/lab", json={"command": "execute"}, headers={**self.headers, "Origin": "https://untrusted.invalid"})
        self.assertEqual(response.status, 403)
        response = await self.client.get("/", headers={"Host": "untrusted.invalid"})
        self.assertEqual(response.status, 403)

    async def test_no_devices_are_invented(self):
        response = await self.client.post("/api/lab", json={"command": "catalog"}, headers=self.headers)
        data = await response.json()
        self.assertEqual(data["entities"], [])
        self.assertEqual(data["areas"], [])

    async def test_bad_catalog_refused_before_contacting_nlu(self):
        response = await self.client.post("/api/lab", json={"command": "import_catalog", "catalog": {"version": 1}}, headers=self.headers)
        self.assertEqual(response.status, 422)
