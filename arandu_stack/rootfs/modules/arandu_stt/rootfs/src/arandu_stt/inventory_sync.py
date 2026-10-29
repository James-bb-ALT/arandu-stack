#!/usr/bin/env python3
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

import websockets

WS_URL = os.environ.get("ARANDU_HA_WS", "ws://supervisor/core/websocket")
DEFAULT_EXPOSED_DOMAINS = {
    "climate", "cover", "fan", "humidifier", "light", "media_player", "scene", "switch", "todo", "vacuum", "water_heater"
}


class HAWebSocket:
    def __init__(self, token: str):
        self.token = token
        self.ws = None
        self.msg_id = 0

    async def __aenter__(self):
        self.ws = await websockets.connect(WS_URL, max_size=32 * 1024 * 1024, open_timeout=20)
        try:
            hello = json.loads(await asyncio.wait_for(self.ws.recv(), 20))
            if hello.get("type") != "auth_required":
                raise RuntimeError("Handshake HA inesperado")
            await self.ws.send(json.dumps({"type": "auth", "access_token": self.token}))
            auth = json.loads(await asyncio.wait_for(self.ws.recv(), 20))
            if auth.get("type") != "auth_ok":
                raise RuntimeError("Autenticação HA falhou")
        except BaseException:
            await self.ws.close()
            raise
        return self

    async def __aexit__(self, *_):
        if self.ws is not None:
            await self.ws.close()

    async def call(self, command: str, **kwargs) -> Any:
        return await asyncio.wait_for(self._call(command, **kwargs), 30)

    async def _call(self, command: str, **kwargs) -> Any:
        self.msg_id += 1
        mid = self.msg_id
        await self.ws.send(json.dumps({"id": mid, "type": command, **kwargs}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") != mid:
                continue
            if msg.get("type") != "result" or not msg.get("success"):
                raise RuntimeError(f"WS {command} falhou")
            return msg.get("result")


def _friendly_state_map(states: list[dict]) -> dict[str, dict]:
    return {s.get("entity_id"): s for s in states if s.get("entity_id")}


def _aliases(value):
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [item for item in value if isinstance(item, str) and item.strip()]
    return []


def _effective_exposure(entity: dict, explicit: dict[str, dict]) -> bool:
    eid = entity.get("entity_id", "")
    conv = (entity.get("options") or {}).get("conversation") or {}
    if "should_expose" in conv:
        return bool(conv["should_expose"])
    if explicit.get(eid, {}).get("conversation") is False:
        return False
    if explicit.get(eid, {}).get("conversation") is True:
        return True
    return eid.split(".", 1)[0] in DEFAULT_EXPOSED_DOMAINS


def build_inventory(areas: list[dict], devices: list[dict], entities: list[dict], states: list[dict], exposed: dict[str, dict], floors: list[dict] | None = None) -> dict:
    area_by_id = {a.get("area_id") or a.get("id"): a for a in areas}
    device_by_id = {d.get("id"): d for d in devices}
    state_by_id = _friendly_state_map(states)
    floor_by_id = {f.get('floor_id') or f.get('id'): f for f in floors or []}

    out_areas = []
    for a in areas:
        aid = a.get("area_id") or a.get("id")
        out_areas.append({"id": aid, "name": a.get("name"), "aliases": a.get("aliases") or [],
                          "floor_id": a.get('floor_id'), "floor": (floor_by_id.get(a.get('floor_id')) or {}).get('name')})

    out_devices = []
    for d in devices:
        area_id = d.get("area_id")
        area = (area_by_id.get(area_id) or {}).get("name")
        out_devices.append({
            "id": d.get("id"), "name": d.get("name"), "name_by_user": d.get("name_by_user"),
            "aliases": d.get("aliases") or [], "area": area, "area_id": area_id,
            "manufacturer": d.get("manufacturer"), "model": d.get("model"),
        })

    out_entities = []
    # YAML automations/scripts/scenes and persons may exist only in states.
    registry_ids = {e.get('entity_id') for e in entities}
    entities = list(entities) + [s for s in states if s.get('entity_id') not in registry_ids
                                and s.get('entity_id', '').split('.')[0] in {'automation', 'scene', 'script', 'person'}]
    for e in entities:
        eid = e.get("entity_id")
        if not eid:
            continue
        state = state_by_id.get(eid) or {}
        attrs = state.get("attributes") or {}
        device = device_by_id.get(e.get("device_id")) or {}
        area_id = e.get("area_id") or device.get("area_id")
        area = (area_by_id.get(area_id) or {}).get("name")
        domain = eid.split(".", 1)[0]
        name = e.get("name") or attrs.get("friendly_name") or e.get("original_name") or eid
        out_entities.append({
            "entity_id": eid,
            "domain": domain,
            "name": name,
            "original_name": e.get("original_name"),
            "aliases": e.get("aliases") or [],
            "assist_aliases": list(dict.fromkeys(_aliases(e.get('assist_aliases')) +
                                    _aliases(attrs.get('assist_aliases')) +
                                    _aliases(((e.get('options') or {}).get('conversation') or {}).get('aliases')))),
            "area": area,
            "area_id": area_id,
            "device": device.get("name_by_user") or device.get("name"),
            "device_id": e.get("device_id"),
            "manufacturer": device.get("manufacturer"),
            "model": device.get("model"),
            "device_class": attrs.get("device_class") or e.get("original_device_class"),
            "entity_category": e.get("entity_category"),
            "platform": e.get("platform"),
            "unit": attrs.get("unit_of_measurement"),
            "options": attrs.get("options"),
            "exposed_to_assist": _effective_exposure(e, exposed),
            "disabled": bool(e.get("disabled_by")),
            "hidden": bool(e.get("hidden_by")),
            "floor_id": (area_by_id.get(area_id) or {}).get('floor_id'),
            "floor": (floor_by_id.get((area_by_id.get(area_id) or {}).get('floor_id')) or {}).get('name'),
        })

    persons = [{"entity_id": e["entity_id"], "name": e["name"], "aliases": e.get("aliases") or []}
               for e in out_entities if e.get("domain") == "person" and e.get("name")]
    scenes = [{"entity_id": e["entity_id"], "name": e["name"], "aliases": e.get("aliases") or []}
              for e in out_entities if e.get("domain") == "scene" and e.get("name")]
    scripts = [{"entity_id": e["entity_id"], "name": e["name"], "aliases": e.get("aliases") or []}
               for e in out_entities if e.get("domain") == "script" and e.get("name")]

    by_domain: dict[str, int] = {}
    for e in out_entities:
        by_domain[e["domain"]] = by_domain.get(e["domain"], 0) + 1

    return {
        "_meta": {
            "gerado_em": datetime.now(timezone.utc).isoformat(),
            "origem": "Home Assistant registries via Supervisor WebSocket proxy",
            "total_entidades": len(out_entities),
            "total_dispositivos": len(out_devices),
            "total_areas": len(out_areas),
            "total_aliases": sum(len(x.get("aliases") or []) for x in out_entities + out_devices + out_areas),
            "entidades_por_dominio": by_domain,
        },
        "areas": out_areas,
        "floors": [{"id": f.get('floor_id') or f.get('id'), "name": f.get('name'), "aliases": f.get('aliases') or []} for f in floors or []],
        "devices": out_devices,
        "persons": persons,
        "scenes": scenes,
        "scripts": scripts,
        "automations": [e for e in out_entities if e['domain'] == 'automation' and e.get('name')],
        "entities": out_entities,
    }


async def sync_inventory(dest: Path) -> dict:
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise RuntimeError("SUPERVISOR_TOKEN ausente; habilite homeassistant_api no App")
    async with HAWebSocket(token) as ha:
        areas = await ha.call("config/area_registry/list")
        devices = await ha.call("config/device_registry/list")
        entities = await ha.call("config/entity_registry/list")
        states = await ha.call("get_states")
        try:
            floors = await ha.call('config/floor_registry/list')
        except Exception:
            floors = []
        try:
            exposure_result = await ha.call("homeassistant/expose_entity/list")
            exposed = (exposure_result or {}).get("exposed_entities") or {}
        except Exception:
            print("[INVENTORY] Aviso: exposição Assist não disponível; usando defaults/registry", flush=True)
            exposed = {}
    inventory = build_inventory(areas, devices, entities, states, exposed, floors)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix('.tmp')
    tmp.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(dest)
    return inventory


def minimal_inventory() -> dict:
    return {"_meta": {"origem": "fallback vazio"}, "areas": [], "floors": [], "devices": [], "persons": [], "scenes": [], "scripts": [], "automations": [], "entities": []}


if __name__ == "__main__":
    path = Path("/data/arandu_ha_inventory.json")
    inv = asyncio.run(sync_inventory(path))
    print(f"[INVENTORY] {len(inv['entities'])} entidades, {len(inv['devices'])} dispositivos, {len(inv['areas'])} áreas", flush=True)
