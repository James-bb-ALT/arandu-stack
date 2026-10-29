import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from wakegen.runner import Generator, VoiceBankBlocked, VoiceReferenceExhausted


class VoiceRecoveryTests(unittest.TestCase):
    def generator(self):
        generator = Generator.__new__(Generator)
        generator.config = {"targets": {"positive": 1}}
        generator.counts = {"positive": 0}
        generator.profiles = [{}, {}]
        generator.store = Mock()
        generator.store.exists.return_value = False
        generator.store.setting.side_effect = lambda key, default=None: default
        generator.engine = Mock()
        generator.status = Mock()
        generator.ready = Mock(return_value=False)
        return generator

    def test_rejected_voice_does_not_stop_next_voice(self):
        generator = self.generator()
        generator.ensure_voice = Mock(side_effect=[VoiceReferenceExhausted("duplicate"), {"split": "train"}])

        def save(*args):
            generator.counts["positive"] += 1

        generator.make_sample = Mock(side_effect=save)
        generator.config["generation"] = {"max_attempts_per_sample": 1}
        with tempfile.TemporaryDirectory() as folder, patch("wakegen.runner.ROOT", Path(folder)), \
                patch("wakegen.runner.check_stop"), patch("wakegen.runner.spec", side_effect=lambda config, cursor: {
                    "speaker_index": cursor, "sample_id": str(cursor), "category": "positive"}):
            generator.run()
        self.assertEqual(generator.counts["positive"], 1)
        generator.store.set_setting.assert_any_call("unavailable_speaker_indices", [0])
        generator.store.fail.assert_called_once()

    def test_corrupt_reference_still_blocks(self):
        generator = self.generator()
        generator.ensure_voice = Mock(side_effect=VoiceBankBlocked("corrupt"))
        with patch("wakegen.runner.check_stop"), patch("wakegen.runner.spec", return_value={
                "speaker_index": 0, "sample_id": "x", "category": "positive"}):
            with self.assertRaisesRegex(VoiceBankBlocked, "corrupt"):
                generator.run()

