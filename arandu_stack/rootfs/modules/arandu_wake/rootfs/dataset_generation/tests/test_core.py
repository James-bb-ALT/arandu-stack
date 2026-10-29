"""Fixtures de funções puras não são evidência de síntese TTS nem entram no dataset."""
import hashlib
from pathlib import Path
import random
import tempfile
import unittest

import numpy as np
import soundfile as sf

from wakegen.audio_validation import augment, canonical_clip, inspect_audio, resample, write_wav_atomic
from wakegen.common import WorkerLock, atomic_json, load_config
from wakegen.store import Store
from wakegen.texts import HARD, GENERAL, contains_wake, spec, wake_count
from wakegen.voice_bank import nearest_similarity, profiles


class ProfilesTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config()

    def test_exact_speaker_splits(self):
        bank = list(profiles(self.config))
        self.assertEqual(len(bank), 200)
        self.assertEqual({s: sum(p["split"] == s for p in bank) for s in ("train", "validation", "test")}, {"train": 160, "validation": 20, "test": 20})
        self.assertEqual(len({p["description"] for p in bank}), 200)
        self.assertEqual(len({p["speaker_id"] for p in bank}), 200)

    def test_profiles_are_reproducible(self):
        self.assertEqual(list(profiles(self.config)), list(profiles(self.config)))

    def test_brazilian_locale_is_required_and_prompted(self):
        for profile in profiles(self.config):
            self.assertEqual(profile["locale"], "pt-BR")
            self.assertIn("native BRAZILIAN Portuguese", profile["description"])
            self.assertEqual(profile["accent_validation"], "pending_listening_review")
        import tempfile
        import yaml
        self.config["generation"]["locale"] = "pt-PT"
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.yaml"
            path.write_text(yaml.safe_dump(self.config), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "brasileiro"):
                load_config(path)

    def test_expansion_500(self):
        self.config["voice_bank"]["profiles"] = 500
        self.assertEqual(len(list(profiles(self.config))), 500)

    def test_non_seed_description_diversity(self):
        bank = list(profiles(self.config))
        self.assertEqual({p["gender_description"] for p in bank}, {"feminina", "masculina", "andrógina"})
        self.assertEqual(len({p["region"] for p in bank}), 6)

    def test_speaker_similarity(self):
        self.assertAlmostEqual(nearest_similarity([1, 0], [("a", [1, 0])])["cosine_similarity"], 1)
        self.assertEqual(nearest_similarity([1, 0], [])["nearest_speaker_id"], None)

    def test_exact_default_class_targets_in_schedule(self):
        counts = {"positive": 0, "negative_hard": 0, "negative_general": 0}
        for i in range(100000):
            counts[spec(self.config, i)["category"]] += 1
        self.assertEqual(counts, self.config["targets"])

    def test_positive_context_mix(self):
        jobs = [spec(self.config, i) for i in range(2000)]
        positives = [j for j in jobs if j["category"] == "positive"]
        self.assertEqual({k: sum(j["kind"] == k for j in positives) for k in ("isolated", "context", "special")}, {"isolated": 700, "context": 200, "special": 100})

    def test_negative_transcripts_clean(self):
        self.assertTrue(contains_wake("ARANDU, está aí?"))
        self.assertFalse(contains_wake("arando"))
        for text in [v[0] for v in HARD] + GENERAL:
            self.assertFalse(contains_wake(text))

    def test_wake_repetition_with_punctuation(self):
        self.assertEqual(wake_count("Arandu. Arandu!"), 2)
        self.assertEqual(wake_count("Arando, Aranda"), 0)


class AudioTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config()
        time = np.arange(24000) / 24000
        self.audio = (0.1 * np.sin(2 * np.pi * 180 * time) * np.sin(np.pi * time)).astype(np.float32)

    def test_reject_silence_and_nonfinite(self):
        self.assertFalse(inspect_audio(np.zeros(16000), 16000, self.config)["valid"])
        self.assertFalse(inspect_audio(np.array([np.nan]), 16000, self.config)["valid"])

    def test_severe_clipping_rejected(self):
        result = inspect_audio(np.ones(16000), 16000, self.config)
        self.assertIn("severe_clipping", result["reasons"])

    def test_resampling_duration(self):
        self.assertEqual(len(resample(self.audio, 24000, 16000)), 16000)

    def test_canonical_clip_reproducible(self):
        a, ma = canonical_clip(self.audio, 24000, self.config, random.Random(1))
        b, mb = canonical_clip(self.audio, 24000, self.config, random.Random(1))
        np.testing.assert_array_equal(a, b)
        self.assertEqual(ma, mb)
        self.assertLess(max(abs(a)), 1)
        self.assertLess(ma["leading_silence_seconds"], 0.23)

    def test_atomic_wav_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.wav"
            checksum = write_wav_atomic(path, self.audio, 24000)
            self.assertEqual(checksum, hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(sf.info(path).subtype, "PCM_16")
            with self.assertRaises(FileExistsError):
                write_wav_atomic(path, self.audio, 24000)

    def test_lazy_augmentation_keeps_source(self):
        before = self.audio.copy()
        out = augment(self.audio, 24000, 1, noise=np.random.default_rng(1).normal(size=24000))
        np.testing.assert_array_equal(self.audio, before)
        self.assertEqual(len(out), len(before))
        self.assertLessEqual(float(np.max(np.abs(out))), 0.981)

    def test_lazy_microphone_and_codec_profiles(self):
        result = augment(self.audio, 24000, 2, microphone_profile="tablet_approx", mulaw_codec=True)
        self.assertEqual(len(result), len(self.audio))
        self.assertTrue(np.isfinite(result).all())
        with self.assertRaises(ValueError):
            augment(self.audio, 24000, 2, microphone_profile="invented-device")


class StoreTests(unittest.TestCase):
    def test_config_identity_freeze_and_allow_target_increase(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            try:
                config = load_config()
                store.freeze(config)
                config["targets"]["positive"] += 2
                store.freeze(config)
                config["seed"] += 1
                with self.assertRaises(ValueError):
                    store.freeze(config)
            finally:
                store.close()

    def test_resume_rebuild_from_sidecar_and_no_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "data/positive/train/unit-fixture.wav"
            checksum = write_wav_atomic(path, np.ones(16000, dtype=np.float32) * 0.01, 16000)
            record = {"sample_id": "unit-fixture", "category": "positive", "speaker_id": "spk_0000", "split": "train",
                      "path": path.relative_to(root).as_posix(), "wav_sha256": checksum,
                      "processing": {"content_hash_before_padding_and_speed": "abc"}}
            atomic_json(root / "data/metadata/samples/unit-fixture.json", record)
            store = Store(root)
            try:
                store.recover()
                store.recover()
                self.assertEqual(store.counts()["positive"], 1)
                lines = (root / "data/manifests/samples.jsonl").read_text().splitlines()
                self.assertEqual(len(lines), 1)
                self.assertTrue(store.duplicate("abc"))
            finally:
                store.close()

    def test_corrupt_wav_not_committed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "data/fixture.wav"
            write_wav_atomic(path, np.zeros(16000, dtype=np.float32), 16000)
            record = {"sample_id": "fixture", "path": "data/fixture.wav", "wav_sha256": "incorrect"}
            store = Store(root)
            try:
                with self.assertRaises(ValueError):
                    store.commit_sample(record)
                self.assertEqual(sum(store.counts().values()), 0)
            finally:
                store.close()

    def test_lock_prevents_duplicate_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "worker.lock"
            with WorkerLock(path):
                with self.assertRaises(RuntimeError):
                    with WorkerLock(path):
                        pass


if __name__ == "__main__":
    unittest.main()
