"""Unit tests for arandu_stack.app.llm_downloads."""
import asyncio
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch

from arandu_stack.app.llm_catalog import MODELS
from arandu_stack.app.llm_downloads import (
    DownloadCancelledError,
    InsufficientDiskSpaceError,
    LLMDownloadManager,
)


class SyntheticGGUFServer(BaseHTTPRequestHandler):
    """Servidor HTTP simples para servir arquivos GGUF sintéticos de teste."""
    content: bytes = b''

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Content-Length', str(len(self.content)))
        self.end_headers()
        self.wfile.write(self.content)

    def log_message(self, format, *args):
        pass  # Silenciar logs na suíte de testes


class TestLLMDownloads(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.downloader = LLMDownloadManager(self.root)

        # Preparar dados sintéticos para um modelo de teste
        self.fake_info = dict(MODELS['tucano'])
        self.fake_size = 1024
        header = b'GGUF'
        body = b'X' * (self.fake_size - len(header))
        self.fake_content = header + body
        self.fake_sha256 = hashlib.sha256(self.fake_content).hexdigest()

        self.fake_info['size'] = self.fake_size
        self.fake_info['sha256'] = self.fake_sha256

        SyntheticGGUFServer.content = self.fake_content
        self.httpd = HTTPServer(('127.0.0.1', 0), SyntheticGGUFServer)
        self.port = self.httpd.server_address[1]
        self.server_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.server_thread.start()
        self.mock_url = f"http://127.0.0.1:{self.port}/fake.gguf"

    async def asyncTearDown(self):
        self.httpd.shutdown()
        self.server_thread.join()
        self.tmp_dir.cleanup()

    async def test_successful_download_and_atomic_promotion(self):
        with patch.dict(MODELS, {'tucano': self.fake_info}):
            progress_updates = []

            def cb(data):
                progress_updates.append(data['state'])

            res = await self.downloader.download_model(
                'tucano',
                custom_url=self.mock_url,
                progress_callback=cb,
            )
            self.assertTrue(res['installed'])
            self.assertEqual(res['state'], 'installed')

            # Verificar se arquivo final está no destino correto
            final_file = self.root / 'tucano' / self.fake_info['file']
            self.assertTrue(final_file.is_file())
            self.assertEqual(final_file.stat().st_size, self.fake_size)

            # Verificar metadados arandu-model.json
            meta_file = self.root / 'tucano' / 'arandu-model.json'
            self.assertTrue(meta_file.is_file())
            meta = json.loads(meta_file.read_text(encoding='utf-8'))
            self.assertEqual(meta['sha256'], self.fake_sha256)

            # Verificar estados de progresso
            self.assertIn('downloading', progress_updates)
            self.assertIn('verifying', progress_updates)
            self.assertIn('installed', progress_updates)

    async def test_insufficient_disk_space_rejection(self):
        with patch.dict(MODELS, {'tucano': self.fake_info}):
            # Simular 1 MB livre no disco
            fake_usage = shutil._ntuple_diskusage(100 * 1024 * 1024, 99 * 1024 * 1024, 1024)
            with patch('shutil.disk_usage', return_value=fake_usage):
                with self.assertRaises(InsufficientDiskSpaceError):
                    await self.downloader.download_model('tucano', custom_url=self.mock_url)

    async def test_download_cancellation(self):
        with patch.dict(MODELS, {'tucano': self.fake_info}):
            # Configurar cancelamento logo no início
            self.downloader.cancel_download('tucano')  # Pré-ativa evento de cancelamento
            # Chamar download e esperar DownloadCancelledError
            self.downloader._cancellation_events['tucano'] = asyncio.Event()
            self.downloader._cancellation_events['tucano'].set()

            with self.assertRaises(DownloadCancelledError):
                await self.downloader.download_model('tucano', custom_url=self.mock_url)

    async def test_delete_model_protection_and_cleanup(self):
        with patch.dict(MODELS, {'tucano': self.fake_info}):
            # Instalar primeiro
            await self.downloader.download_model('tucano', custom_url=self.mock_url)
            self.assertTrue((self.root / 'tucano' / self.fake_info['file']).exists())

            # Tentar remover modelo protegido
            with self.assertRaises(ValueError) as ctx:
                self.downloader.delete_model('tucano', protected_model_id='tucano')
            self.assertIn("ativo em produção", str(ctx.exception))

            # Remover com sucesso quando não protegido
            res = self.downloader.delete_model('tucano', protected_model_id='outra_coisa')
            self.assertTrue(res['deleted'])
            self.assertFalse((self.root / 'tucano').exists())


if __name__ == '__main__':
    unittest.main()
