"""FIXTURE_TECNICA. Optional official HA classes, simulated registries and services.

Run in the pinned HA 2026.9.4 image. This does not start or deploy Home Assistant.
The --root may be an extracted integration ZIP, not the source checkout.
"""
import argparse
import asyncio
import importlib
import json
import socket
import subprocess
import time
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import urllib.request

from homeassistant.components.conversation import ConversationInput
from homeassistant.const import __version__
from homeassistant.core import Context, State
from homeassistant.helpers import device_registry, entity_registry, selector


async def probe(root, endpoint=None):
    sys.path.insert(0, str(root))
    from custom_components.local_nlu.config_flow import LocalNluOptionsFlow
    from custom_components.local_nlu.conversation import LocalNluConversationEntity
    from custom_components.local_nlu.runtime import LocalNluRuntime

    assert __version__ == "2026.9.4", __version__
    options = {"contextual_enabled": True, "bulk_domains": ["fan"], "voice_identity_bindings": {}, "session_ttl": 60}
    user = SimpleNamespace(id="fixture-user", name="Pessoa da fixture", is_active=True, system_generated=False,
                           is_admin=False, permissions=SimpleNamespace(check_entity=lambda entity_id, policy: entity_id == "fan.fixture"))
    inactive = SimpleNamespace(id="inactive-user", name="Inativo", is_active=False, system_generated=False)
    system = SimpleNamespace(id="system-user", name="Sistema", is_active=True, system_generated=True)
    async def lookup(user_id):
        return user if user_id == user.id else None
    auth = SimpleNamespace(async_get_users=AsyncMock(return_value=[user, inactive, system]), async_get_user=lookup)
    registry_entry = SimpleNamespace(id="fixture_fan_registry", entity_id="fan.fixture", area_id="studio", aliases=[],
        name="Ventilador de Ana", original_name="Ventilador de Ana", original_name_unprefixed=None, device_id=None, disabled_by=None, hidden_by=None)
    satellite = SimpleNamespace(id="fixture_satellite_registry", entity_id="assist_satellite.fixture", area_id=None,
                                name="Satélite de teste", disabled_by=None)
    registry = SimpleNamespace(entities={"fan.fixture": registry_entry, "assist_satellite.fixture": satellite})
    device = SimpleNamespace(id="fixture-device", name="Dispositivo de teste", area_id=None, disabled_by=None)
    devices = SimpleNamespace(async_get=lambda device_id: device if device_id == device.id else None)
    areas = SimpleNamespace(areas={"studio": SimpleNamespace(name="Estúdio de Ana", aliases=set(), floor_id=None)})
    state = State("fan.fixture", "on", {"friendly_name": "Ventilador de Ana", "supported_features": 49})
    calls = []
    async def service(domain, name, data, **kw):
        calls.append((domain, name, kw))
    hass = SimpleNamespace(auth=auth, states=SimpleNamespace(get=lambda entity_id: state if entity_id == state.entity_id else None),
                           services=SimpleNamespace(has_service=lambda domain, name: domain == "fan" and name in ("turn_on", "turn_off", "set_percentage"), async_call=service),
                           config=SimpleNamespace(language="pt-BR", time_zone="America/Bahia"))

    class Flow(LocalNluOptionsFlow):
        @property
        def config_entry(self):
            return SimpleNamespace(options=options)

    with patch("homeassistant.helpers.entity_registry.async_get", return_value=registry), \
         patch("homeassistant.helpers.device_registry.async_get", return_value=devices), \
         patch("homeassistant.helpers.area_registry.async_get", return_value=areas), \
         patch("homeassistant.helpers.floor_registry.async_get", return_value=SimpleNamespace(floors={})), \
         patch("homeassistant.components.homeassistant.exposed_entities.async_should_expose", return_value=True):
        flow = Flow()
        flow.hass = hass
        form = await flow.async_step_init()
        fields = {key.schema: value for key, value in form["data_schema"].schema.items()}
        assert isinstance(fields["fallback_user_id"], selector.SelectSelector)
        selected = fields["fallback_user_id"].config["options"]
        assert {row["value"] for row in selected} == {"", user.id}, selected
        assert (await flow.async_step_init({"fallback_user_id": "inactive-user"}))["errors"]["fallback_user_id"] == "invalid_identity_user"
        saved = await flow.async_step_init({"fallback_user_id": user.id})
        assert saved["data"]["bulk_domains"] == ["fan"]
        assert saved["data"]["fallback_user_id"] == user.id
        disabled = await flow.async_step_init({"fallback_user_id": ""})
        assert "fallback_user_id" not in disabled["data"]
        form = await flow.async_step_init({"fallback_user_id": user.id, "configure_identity_bindings": True})
        assert form["step_id"] == "identity_binding"
        saved = await flow.async_step_identity_binding({"satellite_id": satellite.entity_id, "identity_user_id": user.id, "save_and_finish": False})
        assert saved["step_id"] == "identity_binding"
        saved = await flow.async_step_identity_binding({"device_id": device.id, "identity_user_id": user.id, "save_and_finish": True})
        assert saved["data"]["voice_identity_bindings"] == {satellite.entity_id: user.id}
        assert saved["data"]["device_identity_bindings"] == {device.id: user.id}
        removed = await flow.async_step_identity_binding({"existing_binding": "satellite:" + satellite.entity_id, "save_and_finish": True})
        assert not removed["data"]["voice_identity_bindings"]
        # An unavailable origin can still be removed using the saved-binding selector.
        registry.entities.pop(satellite.entity_id)
        removed = await flow.async_step_identity_binding({"existing_binding": "device:" + device.id, "save_and_finish": True})
        assert not removed["data"]["device_identity_bindings"]

        class Client:
            requests = []
            async def send(self, path, payload):
                def request():
                    data = urllib.request.Request(endpoint + path, json.dumps(payload).encode(), {"Content-Type": "application/json"})
                    with urllib.request.urlopen(data, timeout=3) as response:
                        return json.load(response)
                return await asyncio.to_thread(request)
            async def async_catalog_v4(self, payload):
                if endpoint:
                    return await self.send("/v4/catalog", payload)
                return {"version": 4, "status": "catalog_ready", "generation": payload["generation"]}
            async def async_interpret_v4(self, payload):
                self.requests.append(payload)
                if endpoint:
                    return await self.send("/v4/interpret", payload)
                return {"version": 4, "status": "plan", "operations": [{"intent": "fan.turn_off", "action": "turn_off", "targets": [registry_entry.id], "parameters": {}, "evidence": ["friendly_name"], "depends_on": []}]}
        client = Client()
        options["fallback_user_id"] = user.id
        runtime = LocalNluRuntime(hass, client, contextual_enabled=lambda: True, contextual_options=lambda: options)
        agent = LocalNluConversationEntity(SimpleNamespace(runtime_data=runtime, entry_id="fixture-entry"))
        request = ConversationInput(text="Desliga os ventiladores de Ana", context=Context(), conversation_id="fixture",
                                    device_id=None, satellite_id="assist_satellite.fixture", language="pt-BR", agent_id="conversation.arandu_nlu")
        result = await agent.async_process(request)
        assert result.response.as_dict()["speech"]["plain"]["speech"] == "Pronto.", result.response.as_dict()
        assert len(client.requests) == len(calls) == 1
        assert calls[0][2]["context"].user_id == user.id
        assert calls[0][2]["context"].parent_id == request.context.id
        assert request.context.user_id is None
        assert runtime._contextual.identity_diagnostics()["last_source"] == "fallback"

        if endpoint:
            with urllib.request.urlopen(endpoint + "/diagnostics", timeout=3) as response:
                backend = json.load(response)
            assert backend["service_version"] == json.loads((root / "custom_components/local_nlu/manifest.json").read_text())["version"]
            assert 4 in backend["protocols"] and backend["execution"] == "passive"

    for name in ("config_flow", "contextual_runtime", "identity", "conversation"):
        path = Path(importlib.import_module("custom_components.local_nlu." + name).__file__).resolve()
        assert path.is_relative_to(root.resolve()), (name, path, root)
    print(f"Official HA {__version__} options/selectors/ConversationInput/Context/entity + {'real Rust v4' if endpoint else 'fixture client'} + simulated authorized service PASS; root={root}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--binary", type=Path)
    args = parser.parse_args()
    if args.binary:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        endpoint = f"http://127.0.0.1:{port}"
        server = subprocess.Popen([str(args.binary), "serve", "--listen", f"127.0.0.1:{port}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for attempt in range(100):
                try:
                    with urllib.request.urlopen(endpoint + "/health", timeout=.1): break
                except OSError: time.sleep(.02)
            else: raise RuntimeError("artifact Rust server did not start")
            asyncio.run(probe(args.root, endpoint))
        finally:
            server.terminate()
            server.wait(timeout=5)
    else:
        asyncio.run(probe(args.root))
