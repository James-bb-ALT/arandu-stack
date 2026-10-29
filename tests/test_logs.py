"""Manual regression checks for log boundaries; no inference or HA calls."""
import sys
from pathlib import Path
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'arandu_stack/app'))
from logs import LogStore, MAX_BYTES, sanitize

class Logs(unittest.TestCase):
    def test_only_known_sources_can_be_read(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                LogStore(Path(directory)).tail('../../options.json')

    def test_traceback_remains_readable_and_secrets_are_hidden(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LogStore(Path(directory), lambda: {'tts': {'remote_token': 'FIXTURE_SECRET'}})
            path = store.path('tts')
            path.parent.mkdir(parents=True)
            path.write_text('Traceback (most recent call last):\nRuntimeError: Bearer FIXTURE_SECRET\n', encoding='utf-8')
            output = store.tail('tts', 100)['text']
            self.assertIn('Traceback', output)
            self.assertIn('RuntimeError', output)
            self.assertNotIn('FIXTURE_SECRET', output)

    def test_credentials_in_headers_and_urls_are_hidden(self):
        output = sanitize('Authorization: Bearer FIXTURE_SECRET\nhttps://name:pass@example.invalid/?token=FIXTURE_SECRET')
        self.assertNotIn('FIXTURE_SECRET', output)
        self.assertNotIn('name:pass', output)

    def test_large_log_reads_are_bounded_and_keep_final_error(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LogStore(Path(directory))
            path = store.path('stt')
            path.parent.mkdir(parents=True)
            path.write_bytes(b'x' * (MAX_BYTES * 2) + b'\nRuntimeError: final fixture error\n')
            output = store.tail('stt', 100)
            self.assertTrue(output['truncated'])
            self.assertIn('final fixture error', output['text'])
            self.assertLessEqual(len(output['text']), MAX_BYTES)

    def test_empty_source_is_not_a_fabricated_error(self):
        with tempfile.TemporaryDirectory() as directory:
            output = LogStore(Path(directory)).tail('wake')
            self.assertFalse(output['exists'])
            self.assertEqual(output['text'], '')

    def test_log_symlink_cannot_read_outside_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = LogStore(root/'data')
            outside = root/'outside.txt'
            outside.write_text('FIXTURE_PRIVATE', encoding='utf-8')
            path = root/'data/modules/stt/runtime.log'
            path.parent.mkdir(parents=True)
            try:
                path.symlink_to(outside)
            except OSError:
                self.skipTest('Symlinks unavailable in this environment')
            with self.assertRaises(ValueError):
                store.tail('stt')

    def test_control_sequences_do_not_pollute_traceback(self):
        self.assertEqual(sanitize('\x1b[31mRuntimeError\x1b[0m'), 'RuntimeError')
