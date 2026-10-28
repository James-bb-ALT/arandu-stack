"""FIXTURE_TECNICA: manual tests, no Supervisor or model inference."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import AsyncMock

spec = importlib.util.spec_from_file_location('arandu_control', Path(__file__).parents[1] / 'arandu_stack/app/server.py')
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)

class Phrases(unittest.TestCase):
    def test_unicode_and_spaces(self):
        self.assertEqual(control.normalize('  CAFÉ   Sala '), control.normalize('cafe\u0301 sala'))

    def test_duplicate_rejected(self):
        rows = [{'phrase': 'Cinema', 'command': 'apague luz', 'enabled': True},
                {'phrase': ' cinema ', 'command': 'ligue luz', 'enabled': False}]
        with self.assertRaises(ValueError):
            control.validate_phrases(rows)

    def test_arbitrary_fields_rejected(self):
        with self.assertRaises(ValueError):
            control.validate_phrases([{'phrase': 'fixture', 'command': 'fixture', 'enabled': True, 'service': 'arbitrary'}])

    def test_secret_options_not_returned(self):
        self.assertEqual(control.redact({'threads': 2, 'hf_token': 'fixture', 'remote_token': 'fixture'}), {'threads': 2})

class Scope(unittest.IsolatedAsyncioTestCase):
    async def test_only_same_repository_modules(self):
        supervisor = control.Supervisor()
        supervisor.call = AsyncMock(return_value={'slug': 'fixture_arandu_stack'})
        self.assertEqual(await supervisor.slug('nlu'), 'fixture_ptbr_nlu')
        with self.assertRaises(ValueError):
            await supervisor.slug('../other')
