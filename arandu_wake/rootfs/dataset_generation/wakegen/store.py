"""SQLite é autoridade; manifesto incremental pode ser reconstruído por streaming."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

import soundfile as sf

from .common import ROOT, atomic_json, identity_config


class Store:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        (self.root / "state").mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "state/dataset.sqlite", timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS samples(id TEXT PRIMARY KEY, category TEXT, speaker TEXT, split TEXT,
          content_hash TEXT UNIQUE, metadata TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS voices(id TEXT PRIMARY KEY, metadata TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS failures(id TEXT PRIMARY KEY, metadata TEXT NOT NULL);
        """)
        self.db.commit()

    def close(self):
        self.db.close()

    def freeze(self, config):
        expected = identity_config(config)
        previous = self.setting("identity")
        if previous is not None and previous != expected:
            raise ValueError("Configuração de identidade mudou. Use nova dataset_id/pasta; não misture splits ou vozes")
        self.set_setting("identity", expected)

    def setting(self, key, default=None):
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value, ensure_ascii=False)))

    def counts(self):
        result = {"positive": 0, "negative_hard": 0, "negative_general": 0}
        result.update(dict(self.db.execute("SELECT category,count(*) FROM samples GROUP BY category")))
        return result

    def voice(self, ident):
        row = self.db.execute("SELECT metadata FROM voices WHERE id=?", (ident,)).fetchone()
        return json.loads(row[0]) if row else None

    def voices(self):
        return [json.loads(row[0]) for row in self.db.execute("SELECT metadata FROM voices ORDER BY id")]

    def add_voice(self, metadata):
        with self.db:
            self.db.execute("INSERT INTO voices VALUES (?,?)", (metadata["speaker_id"], json.dumps(metadata, ensure_ascii=False)))

    def exists(self, ident):
        return self.db.execute("SELECT 1 FROM samples WHERE id=?", (ident,)).fetchone() is not None

    def duplicate(self, content_hash):
        return self.db.execute("SELECT 1 FROM samples WHERE content_hash=?", (content_hash,)).fetchone() is not None

    def commit_sample(self, metadata, recover=False):
        ident = metadata["sample_id"]
        if self.exists(ident):
            return
        canonical = self.root / metadata["path"]
        info = sf.info(canonical)
        if info.samplerate != 16000 or info.channels != 1 or info.subtype != "PCM_16" or not info.frames:
            raise ValueError("Arquivo canônico inválido antes do commit")
        if hashlib.sha256(canonical.read_bytes()).hexdigest() != metadata["wav_sha256"]:
            raise ValueError("Checksum divergente antes do commit")
        if "native_path" in metadata:
            native = self.root / metadata["native_path"]
            native_info = sf.info(native)
            if native_info.channels != 1 or native_info.frames == 0 or native_info.samplerate != metadata["native_rate"]:
                raise ValueError("WAV nativo incompleto antes do commit")
            if hashlib.sha256(native.read_bytes()).hexdigest() != metadata["native_sha256"]:
                raise ValueError("Checksum nativo divergente antes do commit")
        if not recover:
            atomic_json(self.root / "data/metadata/samples" / f"{ident}.json", metadata)
        with self.db:
            self.db.execute("INSERT INTO samples VALUES (?,?,?,?,?,?)", (
                ident, metadata["category"], metadata["speaker_id"], metadata["split"],
                metadata["processing"]["content_hash_before_padding_and_speed"], json.dumps(metadata, ensure_ascii=False)))
        self.append_manifest(metadata)

    def append_manifest(self, metadata):
        target = self.root / "data/manifests/samples.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as output:
            output.write(json.dumps(metadata, ensure_ascii=False, allow_nan=False) + "\n")
            output.flush()
            os.fsync(output.fileno())

    def recover(self):
        # Sidecar só é escrito após ambos os WAVs. Recupera crash entre sidecar e commit.
        for path in (self.root / "data/metadata/samples").glob("*.json"):
            metadata = json.loads(path.read_text(encoding="utf-8"))
            if not self.exists(metadata["sample_id"]):
                self.commit_sample(metadata, recover=True)
        manifest = self.root / "data/manifests/samples.jsonl"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        temp = manifest.with_suffix(".rebuild.part")
        with temp.open("w", encoding="utf-8") as output:
            for row in self.db.execute("SELECT metadata FROM samples ORDER BY rowid"):
                output.write(row[0] + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, manifest)

    def fail(self, ident, metadata):
        atomic_json(self.root / "data/rejected" / f"{ident}.json", metadata)
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO failures VALUES (?,?)", (ident, json.dumps(metadata, ensure_ascii=False)))

    def records(self):
        for row in self.db.execute("SELECT metadata FROM samples ORDER BY rowid"):
            yield json.loads(row[0])
