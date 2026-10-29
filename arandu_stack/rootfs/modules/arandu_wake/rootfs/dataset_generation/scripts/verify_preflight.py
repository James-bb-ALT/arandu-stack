"""Verifica síntese já concluída e recuperação idempotente antes do lançamento."""
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wakegen.common import ROOT, WorkerLock, atomic_json, load_config, now
from wakegen.store import Store


def main():
    evidence = json.loads((ROOT / "state/preflight.json").read_text(encoding="utf-8"))
    if not evidence.get("ready"):
        raise ValueError("Preflight não declarou áudio real pronto")
    with WorkerLock():
        store = Store()
        try:
            store.freeze(load_config())
            if evidence["config_identity"] != store.setting("identity"):
                raise ValueError("Preflight pertence a outra configuração")
            before = store.counts()
            store.recover()
            after = store.counts()
            if before != after:
                raise RuntimeError("Contagem mudou inesperadamente na recuperação do preflight")
            for record in store.records():
                if record["model"]["repo"] != "Qwen/Qwen3-TTS-12Hz-0.6B-Base":
                    raise ValueError("Preflight não veio do modelo esperado")
                for field, checksum in (("path", "wav_sha256"), ("native_path", "native_sha256")):
                    if hashlib.sha256((ROOT / record[field]).read_bytes()).hexdigest() != record[checksum]:
                        raise ValueError("WAV divergente no preflight")
            lines = 0
            with (ROOT / "data/manifests/samples.jsonl").open(encoding="utf-8") as manifest:
                for line in manifest:
                    json.loads(line)
                    lines += 1
            if lines != sum(after.values()) or after["positive"] < 1 or after["negative_hard"] + after["negative_general"] < 1:
                raise ValueError("Preflight incompleto")
            result = {"verified": True, "checked_at": now(), "counts": after, "manifest_records": lines,
                      "resume_recovery_idempotent": True, "config_identity": store.setting("identity"),
                      "real_tts": True, "pronunciation": "review_not_human_verified"}
            atomic_json(ROOT / "state/preflight_verified.json", result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        finally:
            store.close()


if __name__ == "__main__":
    main()
