import unittest
from unittest.mock import Mock, call, patch

from wakegen.runner import Generator, VoiceBankBlocked, VoiceReferenceExhausted


class BankPhaseTests(unittest.TestCase):
    def generator(self):
        generator = Generator.__new__(Generator)
        generator.profiles = [{}, {}, {}]
        generator.status = Mock()
        generator.store = Mock()
        generator.store.voices.return_value = []
        generator.ensure_voice = Mock()
        return generator

    def test_all_references_before_any_base_validation(self):
        generator = self.generator()
        with patch("wakegen.runner.check_stop"):
            generator.prepare_voice_bank(set())
        self.assertEqual(generator.ensure_voice.call_args_list, [
            call(0, reference_only=True), call(1, reference_only=True), call(2, reference_only=True),
            call(0, allow_design=False), call(1, allow_design=False), call(2, allow_design=False)])

    def test_failed_reference_preserved_and_skipped(self):
        generator = self.generator()
        generator.ensure_voice.side_effect = [None, VoiceReferenceExhausted("QC"), None, None, None]
        unavailable = set()
        with patch("wakegen.runner.check_stop"):
            generator.prepare_voice_bank(unavailable)
        self.assertEqual(unavailable, {1})
        self.assertNotIn(call(1, allow_design=False), generator.ensure_voice.call_args_list)

    def test_corrupt_reference_stops_before_validation(self):
        generator = self.generator()
        generator.ensure_voice.side_effect = VoiceBankBlocked("corrupt")
        with patch("wakegen.runner.check_stop"), self.assertRaises(VoiceBankBlocked):
            generator.prepare_voice_bank(set())
        generator.ensure_voice.assert_called_once_with(0, reference_only=True)
