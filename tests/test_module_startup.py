"""Manual regression tests; mocked services, no inference or downloads."""
import importlib.util
import json
from pathlib import Path
import runpy
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import patch
import urllib.error

ROOT = Path(__file__).parents[1]


class ModuleStartup(unittest.TestCase):
    def test_tts_does_not_receive_dispatcher_argument(self):
        addon = ModuleType('arandu_tts.addon_main')
        server = ModuleType('arandu_tts.server')
        voices = ModuleType('bundled_voices')
        addon.configure = lambda options: None
        voices.prepare = lambda path: None
        seen = []
        server.main = lambda: seen.append(list(sys.argv))
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'options.json').write_text(json.dumps({'max_chars': 3000}))
            modules = {'arandu_tts': ModuleType('arandu_tts'),
                       'arandu_tts.addon_main': addon, 'arandu_tts.server': server,
                       'bundled_voices': voices}
            with patch.dict(sys.modules, modules), patch.dict('os.environ', {'ARANDU_MODULE_DATA': folder}), patch.object(sys, 'argv', ['module_worker.py', 'tts']):
                runpy.run_path(str(ROOT / 'arandu_stack/app/module_worker.py'), run_name='__main__')
        self.assertEqual(seen, [[str(ROOT / 'arandu_stack/app/module_worker.py')]])

    def test_stt_failed_download_reports_public_url_and_removes_partial(self):
        spec = importlib.util.spec_from_file_location('stt_download', ROOT / 'arandu_stack/rootfs/modules/arandu_stt/rootfs/src/arandu_stt/model_download.py')
        download = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(download)
        self.assertIn('/arandu-stack/releases/', download.ADAPTED_URL)
        error = urllib.error.HTTPError(download.ADAPTED_URL, 404, 'Not Found', {}, None)
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(download.urllib.request, 'urlopen', side_effect=error):
                with self.assertRaisesRegex(RuntimeError, 'HTTP 404'):
                    download.ensure_model(Path(folder))
            self.assertEqual(list(Path(folder).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
