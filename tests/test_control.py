"""FIXTURE_TECNICA: manual tests, no Supervisor or model inference."""
import importlib.util
from pathlib import Path
import unittest
import sys
import tempfile
import os
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

sys.path.insert(0, str(Path(__file__).parents[1] / 'arandu_stack/app'))
from modules import ModuleManager, validate_options, DEFAULTS
from settings import FIELDS

spec = importlib.util.spec_from_file_location('arandu_control', Path(__file__).parents[1] / 'arandu_stack/app/server.py')
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)

class Phrases(unittest.TestCase):
    def test_every_option_has_a_typed_ui_field(self):
        for module, defaults in DEFAULTS.items():
            self.assertEqual({row['id'] for row in FIELDS[module]}, set(defaults) - {'enabled'})

    def test_only_supported_wake_words_are_accepted(self):
        self.assertEqual(validate_options('wake', {'wake_word': 'hey_jarvis'})['wake_word'], 'hey_jarvis')
        with self.assertRaises(ValueError):
            validate_options('wake', {'wake_word': 'not_a_supported_model'})

    def test_runtime_fields_have_bounds(self):
        with self.assertRaises(ValueError):
            validate_options('llm', {'max_tokens': 100000})
        with self.assertRaises(ValueError):
            validate_options('decision', {'threshold': .1})

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
    async def test_module_start_failure_does_not_take_down_log_page(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = ModuleManager(Path(directory))
            manager.config['stt']['enabled'] = True
            async def start(key, persist=True):
                if key == 'stt':
                    raise OSError('FIXTURE_START_FAILURE')
            with patch.object(manager, 'start', AsyncMock(side_effect=start)):
                await manager.startup()
            self.assertIn('FIXTURE_START_FAILURE', manager.failures['stt'])

    async def test_trained_voice_migration_preserves_later_user_choice(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path/'modules.json').write_text(json.dumps({'tts': {'voice': 'pt_BR-faber-medium'}}), encoding='utf-8')
            manager = ModuleManager(path, local=True)
            self.assertEqual(manager.config['tts']['voice'], 'pt_BR-arandu-medium')
            self.assertTrue((path/'modules-v1-backup.json').is_file())
            await manager.configure('tts', {'voice': 'pt_BR-faber-medium'})
            self.assertEqual(ModuleManager(path, local=True).config['tts']['voice'], 'pt_BR-faber-medium')

    async def test_partial_form_keeps_existing_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = ModuleManager(Path(directory), local=True)
            manager.config['tts']['hf_token'] = 'FIXTURE_TECNICA_TOKEN'
            await manager.configure('tts', {'cpu_threads': 3})
            self.assertEqual(manager.config['tts']['hf_token'], 'FIXTURE_TECNICA_TOKEN')
    async def test_only_known_internal_modules(self):
        with self.assertRaises(ValueError):
            validate_options('../other', {})

    async def test_options_persist_in_single_app(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = ModuleManager(Path(directory), local=True)
            await manager.configure('decision', {'threads': 3})
            restored = ModuleManager(Path(directory), local=True)
            self.assertEqual(restored.config['decision']['threads'], 3)
            self.assertFalse(restored.config['decision']['enabled'])

    async def test_invalid_runtime_cannot_be_started(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = ModuleManager(Path(directory), local=True)
            with patch.object(manager, 'installed', return_value=False):
                with self.assertRaises(ValueError):
                    await manager.start('decision')

    async def test_enabled_changes_require_lifecycle_control(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = ModuleManager(Path(directory), local=True)
            with self.assertRaises(ValueError):
                await manager.configure('decision', {'enabled': True})

    async def test_llm_process_is_owned_and_does_not_receive_ha_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = ModuleManager(Path(directory), local=True)
            process = SimpleNamespace(pid=987654, returncode=None)
            async def finished():
                process.returncode = 0
                return 0
            process.wait = AsyncMock(side_effect=finished)
            with patch.dict(os.environ, {'SUPERVISOR_TOKEN': 'FIXTURE_TECNICA_TOKEN', 'HA_TOKEN': 'FIXTURE_TECNICA_TOKEN'}), patch.object(manager, 'installed', return_value=True), patch('modules.asyncio.create_subprocess_exec', AsyncMock(return_value=process)) as spawn, patch('modules.os.killpg'):
                await manager.start('llm')
                self.assertNotIn('SUPERVISOR_TOKEN', spawn.call_args.kwargs['env'])
                self.assertNotIn('HA_TOKEN', spawn.call_args.kwargs['env'])
                self.assertTrue(spawn.call_args.kwargs['start_new_session'])
                await manager.close()
