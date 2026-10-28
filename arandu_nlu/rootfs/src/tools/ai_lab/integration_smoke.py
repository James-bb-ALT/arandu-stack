"""Real Rust HTTP + real neural worker + existing HA executor, with simulated HA state only."""
import asyncio
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "mlp"))
from test_contextual import hass_
from custom_components.local_nlu.ai_lab import Lab
from custom_components.local_nlu.client import LocalNluClient


async def main():
    import aiohttp
    hass = hass_()
    user = hass.auth.user
    user.id = "fixture-user"
    core = ModuleType("homeassistant.core")
    core.Context = lambda user_id: SimpleNamespace(user_id=user_id)
    sys.modules["homeassistant.core"] = core
    async with aiohttp.ClientSession() as session:
        client = LocalNluClient(session, "http://127.0.0.1:11555")
        entry = SimpleNamespace(entry_id="fixture", options={}, runtime_data=SimpleNamespace(_client=client))
        hass.config_entries = SimpleNamespace(async_entries=lambda domain: [entry])
        lab = Lab(hass)
        first = await lab._handle(user, {"command": "run", "text": "Liga a luz da sala"}, session, "http://127.0.0.1:11556")
        assert first["source"] == "nlu", first
        assert first["operations_validated"]
        assert first["stages"]["decision"]["status"] == "skipped"
        second = await lab._handle(user, {"command": "run", "text": "Acende a lua"}, session, "http://127.0.0.1:11556")
        assert second["stages"]["decision"]["status"] != "skipped"
        assert second["stages"]["llm"]["status"] != "skipped"
        assert not second["operations_validated"], second
        assert not hass.services.calls
        report = {"fixture": "FIXTURE_TECNICA; actual Rust and neural inference; simulated HA registries/state/services", "results": [first, second]}
        path = ROOT / "target" / "ai-lab-integration-smoke.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print("Integration PASS: real Rust short circuit; real Decision/LLM fallback; unknown target rejected; no effects")


if __name__ == "__main__":
    asyncio.run(main())
