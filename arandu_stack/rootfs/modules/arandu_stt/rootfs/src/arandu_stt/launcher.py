#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from inventory_sync import minimal_inventory, sync_inventory
from model_download import ADAPTED_VERSION, ensure_model

DATA = Path(os.environ.get('ARANDU_MODULE_DATA', '/data'))
OPTIONS = DATA / "options.json"
INVENTORY = DATA / "arandu_ha_inventory.json"
MODEL_DIR = DATA / "models" / "int8"


def options() -> dict:
    defaults = {"model_variant": "adapted", "threads": 2, "padding_ms": 0, "profile": "full", "debug_text": False, "refresh_inventory": True,
                "lexical_enabled": True, "closed_vocabulary": True, "generic_weight": 7,
                "home_assistant_weight": 10, "inventory_refresh_seconds": 60}
    if OPTIONS.is_file():
        defaults.update(json.loads(OPTIONS.read_text(encoding="utf-8")))
    return defaults


def refresh_inventory(required: bool) -> None:
    if not required and not INVENTORY.is_file():
        INVENTORY.parent.mkdir(parents=True, exist_ok=True)
        INVENTORY.write_text(json.dumps(minimal_inventory(), ensure_ascii=False) + '\n', encoding='utf-8')
        return
    if not required and INVENTORY.is_file():
        print("[INVENTORY] Reutilizando inventário persistente", flush=True)
        return
    for attempt in range(1, 16):
        try:
            inv = asyncio.run(sync_inventory(INVENTORY))
            print(f"[INVENTORY] OK: {len(inv['entities'])} entidades / {len(inv['devices'])} dispositivos / {len(inv['areas'])} áreas", flush=True)
            return
        except Exception as exc:
            print(f"[INVENTORY] Tentativa {attempt}/15 falhou: {type(exc).__name__}", flush=True)
            time.sleep(2)
    if INVENTORY.is_file():
        print("[INVENTORY] Usando inventário anterior após falha de atualização", flush=True)
        return
    print("[INVENTORY] Sem inventário HA; iniciando com léxico genérico", flush=True)
    INVENTORY.parent.mkdir(parents=True, exist_ok=True)
    INVENTORY.write_text(json.dumps(minimal_inventory(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    cfg = options()
    variant = str(cfg.get("model_variant", "adapted"))
    model_dir = MODEL_DIR if variant == "original" else DATA / "models" / ADAPTED_VERSION
    if cfg.get('auto_download', True):
        ensure_model(model_dir, variant=variant)
    refresh_inventory(bool(cfg.get("refresh_inventory", True)))

    cmd = [
        sys.executable, str(Path(__file__).with_name('server.py')),
        "--model-dir", str(model_dir),
        "--inventory", str(INVENTORY),
        "--uri", "tcp://0.0.0.0:10350",
        *(["--no-discovery"] if os.environ.get('ARANDU_MODULE_DATA') else []),
        "--threads", str(int(cfg.get("threads", 2))),
        "--padding-ms", str(int(cfg.get("padding_ms", 0))),
        "--profile", str(cfg.get("profile", "full")),
        "--metrics", str(DATA / 'metrics.jsonl'),
        "--inventory-refresh-seconds", str(int(cfg.get('inventory_refresh_seconds', 60))),
        "--generic-weight", str(cfg.get('generic_weight', 7)),
        "--ha-weight", str(cfg.get('home_assistant_weight', 10)),
    ]
    if cfg.get("debug_text"):
        cmd.append("--debug-text")
    if not cfg.get('lexical_enabled', True):
        cmd.append('--lexical-disabled')
    if not cfg.get('closed_vocabulary', True):
        cmd.append('--open-vocabulary')
    if not cfg.get('refresh_inventory', True):
        cmd.append('--no-ha-refresh')
    print("[ARANDU] Iniciando servidor Wyoming", flush=True)
    os.execv(sys.executable, cmd)


if __name__ == "__main__":
    main()
