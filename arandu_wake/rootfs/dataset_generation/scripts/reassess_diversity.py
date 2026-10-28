"""Migração auditável: reavaliar referências rejeitadas apenas por cosine não calibrado."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wakegen.common import ROOT, WorkerLock, atomic_json, load_config, now
from wakegen.store import Store


def main():
    with WorkerLock():
        store = Store()
        try:
            config = load_config()
            store.freeze(config)
            candidates = []
            for path in sorted((ROOT / "data/voices").glob("*/reference_attempt_*.json")):
                candidate = json.loads(path.read_text(encoding="utf-8"))
                if candidate.get("rejection_reason") != "speaker_embedding_near_duplicate":
                    continue
                reference = (ROOT / candidate["reference_path"]).resolve()
                if not reference.is_relative_to(ROOT) or hashlib.sha256(reference.read_bytes()).hexdigest() != candidate["reference_sha256"]:
                    raise ValueError(f"Referência divergente: {path}")
                candidates.append((path, candidate))
            if not candidates:
                print("Nenhuma referência antiga pendente de reavaliação")
                return
            archive = ROOT / "archive" / ("diversity-policy-v1-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
            archive.mkdir(parents=True)
            backup = sqlite3.connect(archive / "dataset.sqlite")
            try:
                store.db.backup(backup)
            finally:
                backup.close()
            indices = set()
            for path, candidate in candidates:
                saved = archive / path.relative_to(ROOT)
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, saved)
                candidate["previous_rejection"] = candidate.pop("rejection_reason")
                candidate["rejected"] = False
                atomic_json(path, candidate)
                indices.add(candidate["index"])
            previous_cursor = store.setting("cursor", 0)
            new_cursor = min(previous_cursor, min(indices) * config["voice_bank"]["samples_per_turn"])
            unavailable = set(store.setting("unavailable_speaker_indices", [])) - indices
            store.set_setting("unavailable_speaker_indices", sorted(unavailable))
            store.set_setting("cursor", new_cursor)
            evidence = {"policy": "uncalibrated_cosine_review_v2", "changed_at": now(),
                        "references_to_reassess": len(candidates), "profiles_to_reassess": len(indices),
                        "previous_cursor": previous_cursor, "new_cursor": new_cursor,
                        "archive": str(archive), "original_audio_preserved": True}
            store.set_setting("diversity_policy", evidence)
            atomic_json(ROOT / "state/diversity_policy_migration.json", evidence)
            print(json.dumps(evidence))
        finally:
            store.close()


if __name__ == "__main__":
    main()
