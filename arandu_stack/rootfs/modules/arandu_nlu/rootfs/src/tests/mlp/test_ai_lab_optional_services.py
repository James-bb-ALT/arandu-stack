"""FIXTURE_TECNICA: optional package discovery; no sockets or inference."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch


@unittest.skipUnless(importlib.util.find_spec("aiohttp"), "optional aiohttp")
class OptionalPackages(unittest.IsolatedAsyncioTestCase):
    def module(self):
        spec = importlib.util.spec_from_file_location("arandu_optional_test", Path(__file__).resolve().parents[2] / "tools/ai_lab/optional_services.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    async def test_missing_packages_are_unavailable(self):
        module = self.module()
        with patch.object(module, "request", AsyncMock(side_effect=ValueError("absent"))):
            result = await module.statuses()
        self.assertEqual(set(result), {"e5-small", "tucano"})
        self.assertTrue(all(not row["available"] for row in result.values()))

    async def test_one_package_does_not_require_the_other(self):
        module = self.module()
        async def status(key, path):
            if key == "tucano":
                raise ValueError("absent")
            return {"package": "arandu-decision", "protocol": "arandu-inference-v1",
                    "models": [{"id": key, "installed": True, "available": True}]}
        with patch.object(module, "request", status):
            result = await module.statuses()
        self.assertTrue(result["e5-small"]["available"])
        self.assertFalse(result["tucano"]["available"])

    async def test_wrong_protocol_is_not_accepted(self):
        module = self.module()
        with patch.object(module, "request", AsyncMock(return_value={"package": "arandu-decision", "protocol": "other"})):
            result = await module.statuses()
        self.assertTrue(all(not row["available"] for row in result.values()))
