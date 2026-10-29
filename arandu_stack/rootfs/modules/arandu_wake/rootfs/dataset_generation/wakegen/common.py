from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def stable_seed(*parts):
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16) & 0x7FFFFFFF


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{os.getpid()}.part")
    with temp.open("w", encoding="utf-8") as out:
        json.dump(value, out, ensure_ascii=False, indent=2, allow_nan=False)
        out.flush()
        os.fsync(out.fileno())
    # Leitores/antivírus Windows podem manter o destino aberto por milissegundos.
    for attempt in range(20):
        try:
            os.replace(temp, path)
            break
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.05)


def load_config(path=None):
    config = yaml.safe_load(Path(path or ROOT / "config.yaml").read_text(encoding="utf-8-sig"))
    if config.get("schema_version") != 1:
        raise ValueError("schema_version inválido")
    targets = config["targets"]
    if set(targets) != {"positive", "negative_hard", "negative_general"} or any(type(v) is not int or v < 0 for v in targets.values()):
        raise ValueError("Metas devem ser inteiros não negativos por classe")
    bank = config["voice_bank"]
    if bank["profiles"] < 10 or bank["samples_per_turn"] != 8:
        raise ValueError("Use pelo menos 10 perfis e samples_per_turn=8")
    if set(bank["splits"]) != {"train", "validation", "test"} or abs(sum(bank["splits"].values()) - 1) > 1e-8:
        raise ValueError("Splits devem somar 1")
    if min(bank["splits"].values()) <= 0:
        raise ValueError("Todos os splits devem ter vozes")
    fractions = [config["generation"][k] for k in ("isolated_fraction", "context_fraction", "special_fraction")]
    if abs(sum(fractions) - 1) > 1e-8 or min(fractions) < 0:
        raise ValueError("Distribuição dos positivos inválida")
    if config["audio"]["sample_rate"] != 16000:
        raise ValueError("Formato canônico deve ser 16 kHz")
    if config["generation"]["language"] != "Portuguese":
        raise ValueError("Esta versão exige Portuguese")
    if config["generation"].get("locale") != "pt-BR":
        raise ValueError("O dataset exige português brasileiro (pt-BR)")
    if config["resources"]["device"] not in {"auto", "cpu", "cuda"}:
        raise ValueError("resources.device deve ser auto, cpu ou cuda")
    return config


def identity_config(config):
    return {k: config[k] for k in ("dataset_id", "seed", "voice_bank", "models", "generation", "audio", "asr")}


class WorkerLock:
    def __init__(self, path=None):
        self.path = Path(path or ROOT / "state/worker.lock")
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            raise RuntimeError("Já existe um worker Arandu Wake nesta pasta")
        return self

    def __exit__(self, *_):
        self.handle.close()


class StopRequested(Exception):
    pass


def check_stop():
    if (ROOT / "state/STOP_REQUESTED").exists():
        raise StopRequested()


def wait_interruptibly(seconds):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        check_stop()
        time.sleep(min(1, max(0, until - time.monotonic())))
