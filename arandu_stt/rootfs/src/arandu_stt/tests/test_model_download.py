import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import model_download as downloader


class TestAdaptedDownload(unittest.TestCase):
    def test_download_verified_cached_and_original_preserved(self):
        payloads = {"model.onnx": b"trained model", "tokens.txt": b"original tokens"}
        hashes = {name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()}
        def response(request, timeout):
            return io.BytesIO(payloads[request.full_url.rsplit('/', 1)[-1]])
        with tempfile.TemporaryDirectory() as folder, patch.object(downloader, 'ADAPTED_HASHES', hashes):
            root = Path(folder)
            original = root / 'int8'
            original.mkdir()
            (original / 'model.int8.onnx').write_bytes(b'previous model')
            adapted = root / downloader.ADAPTED_VERSION
            with patch.object(downloader.urllib.request, 'urlopen', side_effect=response) as download:
                manifest = downloader.ensure_model(adapted)
                self.assertEqual(download.call_count, 2)
            with patch.object(downloader.urllib.request, 'urlopen', side_effect=AssertionError('network')):
                self.assertEqual(downloader.ensure_model(adapted), manifest)
            self.assertEqual((original / 'model.int8.onnx').read_bytes(), b'previous model')
            self.assertEqual(json.loads((adapted / 'manifest.json').read_text())['model_filename'], 'model.onnx')

    def test_invalid_download_never_replaces_existing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / 'model.onnx'
            target.write_bytes(b'previous file')
            with patch.object(downloader.urllib.request, 'urlopen', return_value=io.BytesIO(b'corrupt')):
                with self.assertRaisesRegex(RuntimeError, 'SHA256'):
                    downloader.ensure_model(root)
            self.assertEqual(target.read_bytes(), b'previous file')
            self.assertEqual(list(root.glob('*.download')), [])
            self.assertFalse((root / 'manifest.json').exists())

    def test_explicit_original_uses_existing_downloader(self):
        with patch.object(downloader, 'ensure_original_model', return_value={'status': 'cached'}) as original:
            self.assertEqual(downloader.ensure_model(Path('old'), 'original'), {'status': 'cached'})
            original.assert_called_once_with(Path('old'))


if __name__ == '__main__':
    unittest.main()
