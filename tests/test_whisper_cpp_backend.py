"""Tests for whisper.cpp STT backend, catalog, fixed Portuguese policy and warm benchmarks."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
import wave

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'arandu_stack/app'))
sys.path.insert(0, str(ROOT / 'arandu_stack/rootfs/modules/arandu_stt/rootfs/src/arandu_stt'))

import backends
import lab_worker
import model_catalog as catalog
from modules import validate_options
from stt_errors import AranduRuntimeUnavailableError
from stt_lab import STTLab, WorkerExecutionError
from stt_processing import distance, edit_operations, evaluate
from whisper_cpp_backend import WhisperCppBackend, validate_fixed_portuguese


def _wav_bytes(seconds: float = 0.4, rate: int = 16000, amp: int = 2000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        n = int(rate * seconds)
        samples = (np.sin(np.linspace(0, 20 * np.pi, n)) * amp).astype('<i2')
        wf.writeframes(samples.tobytes())
    return buf.getvalue()


def _create_fake_whisper_server(directory: Path, mode: str = 'ok') -> Path:
    """Create a runnable script implementing whisper-server /health and /inference HTTP endpoints."""
    server_py = directory / 'fake_whisper_server.py'
    args_log = directory / 'server_args.json'
    calls_log = directory / 'server_calls.jsonl'
    server_py.write_text(
        f"""
import argparse, json, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ARGS_LOG = {json.dumps(str(args_log))}
CALLS_LOG = {json.dumps(str(calls_log))}
MODE = {json.dumps(mode)}

with open(ARGS_LOG, 'w', encoding='utf-8') as f:
    json.dump(sys.argv[1:], f)

if MODE == 'oom_on_load':
    sys.stderr.write('ggml_backend_cpu_buffer_type: std::bad_alloc out of memory\\n')
    sys.stderr.flush()
    sys.exit(137)

parser = argparse.ArgumentParser(add_help=False)
parser.add_argument('--host', default='127.0.0.1')
parser.add_argument('--port', type=int, default=8080)
known, unknown = parser.parse_known_args()
if '--no-context' in unknown:
    sys.stderr.write('error: unknown argument: --no-context\\n')
    sys.exit(0)

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return
    def do_GET(self):
        if self.path == '/health':
            body = json.dumps({{'status': 'ok'}}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()
    def do_POST(self):
        length = int(self.headers.get('Content-Length', '0'))
        raw = self.rfile.read(length)
        text_body = raw.decode('latin1', errors='replace')
        with open(CALLS_LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps({{'path': self.path, 'has_pt': 'name="language"\\r\\n\\r\\npt' in text_body, 'no_translate': 'name="translate"\\r\\n\\r\\nfalse' in text_body}}) + '\\n')
        if MODE == 'slow':
            time.sleep(0.8)
        if MODE == 'http500':
            body = b'{{"error":"internal failure"}}'
            self.send_response(500)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = json.dumps({{'text': 'liga a luz da sala'}}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

ThreadingHTTPServer((known.host, known.port), Handler).serve_forever()
""",
        encoding='utf-8',
    )
    if os.name == 'nt':
        wrapper = directory / 'whisper-server.cmd'
        wrapper.write_text(f'@echo off\r\n"{sys.executable}" "{server_py}" %*\r\n', encoding='utf-8')
    else:
        wrapper = directory / 'whisper-server'
        wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{server_py}" "$@"\n', encoding='utf-8')
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return wrapper


class WhisperCppCatalogTests(unittest.TestCase):
    def test_catalog_and_registry_include_whisper_cpp_models_without_breaking_existing(self):
        expected_existing = {
            'fastconformer', 'whisper_tiny', 'whisper_base', 'whisper_small',
            'parakeet_ptbr_30s', 'parakeet_tagarela', 'orukeet_int8', 'granite_turboctc_q4',
            'parakeet_ultra_q8', 'parakeet_ultra_q4', 'phonon_2_5', 'nemotron_unified_0_6b',
        }
        self.assertTrue(expected_existing.issubset(set(catalog.CATALOG)))
        self.assertEqual(len(catalog.CATALOG), 14)
        self.assertEqual(catalog.CATALOG['whisper_small']['runtime'], 'faster-whisper')

        small = catalog.CATALOG['whisper_cpp_small']
        med = catalog.CATALOG['whisper_cpp_medium']
        self.assertEqual(small['runtime'], 'whisper.cpp')
        self.assertEqual(med['runtime'], 'whisper.cpp')
        self.assertEqual(small['revision'], '5359861c739e955e79d9a303bcbc70fb988958b1')
        self.assertEqual(med['revision'], '5359861c739e955e79d9a303bcbc70fb988958b1')
        self.assertEqual(small['files'], ['ggml-small.bin'])
        self.assertEqual(med['files'], ['ggml-medium.bin'])
        self.assertEqual(small['file_sizes']['ggml-small.bin'], 487601967)
        self.assertEqual(med['file_sizes']['ggml-medium.bin'], 1533763059)
        self.assertEqual(small['language'], 'pt')
        self.assertTrue(small['language_fixed'])
        self.assertTrue(med['language_fixed'])

        registry = json.loads((ROOT / 'arandu_stack/model_registry/models.json').read_text(encoding='utf-8'))
        self.assertEqual(registry['counts']['stt'], 15)
        reg_ids = {m['id']: m for m in registry['items']}
        self.assertIn('whisper_cpp_small', reg_ids)
        self.assertIn('whisper_cpp_medium', reg_ids)
        self.assertEqual(reg_ids['whisper_cpp_small']['runtime'], 'whisper.cpp')
        self.assertEqual(reg_ids['whisper_cpp_medium']['runtime'], 'whisper.cpp')

    def test_validate_options_accepts_whisper_cpp_models(self):
        for model_id in ('whisper_cpp_small', 'whisper_cpp_medium'):
            out = validate_options('stt', {'stt_model': model_id, 'threads': 2, 'resident_policy': 'LAZY'})
            self.assertEqual(out['stt_model'], model_id)

    def test_verify_size_check_and_incomplete_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = catalog.location(root, 'whisper_cpp_small')
            model_dir.mkdir(parents=True)
            bin_path = model_dir / 'ggml-small.bin'
            bin_path.write_bytes(b'truncated')
            manifest = {
                'id': 'whisper_cpp_small',
                'repo': 'ggerganov/whisper.cpp',
                'revision': catalog.CATALOG['whisper_cpp_small']['revision'],
                'sha256': {'ggml-small.bin': catalog.CATALOG['whisper_cpp_small']['sha256']['ggml-small.bin']},
            }
            (model_dir / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            info = catalog.describe(root, 'whisper_cpp_small')
            self.assertEqual(info['state'], 'error')
            self.assertTrue(info['incomplete'])
            with self.assertRaisesRegex(ValueError, 'Tamanho do arquivo inválido'):
                catalog.verify(root, 'whisper_cpp_small')

    def test_install_progress_and_marker_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = b'ggml-small-fake-binary-content'
            digest = hashlib.sha256(payload).hexdigest()
            patched_entry = {
                **catalog.CATALOG['whisper_cpp_small'],
                'sha256': {'ggml-small.bin': digest},
                'file_sizes': {'ggml-small.bin': len(payload)},
                'download_bytes': len(payload),
                'installed_bytes': len(payload),
            }
            rev = patched_entry['revision']
            fake_info = SimpleNamespace(
                sha=rev,
                siblings=[SimpleNamespace(rfilename='ggml-small.bin', lfs=SimpleNamespace(sha256=digest))],
            )
            fake_api = SimpleNamespace(model_info=lambda **_k: fake_info)
            def fake_snapshot(**kwargs):
                dest = Path(kwargs['local_dir']) / 'ggml-small.bin'
                dest.write_bytes(payload)
            events = []
            def on_progress(state, **extra):
                events.append((state, extra))
                status = root / '.install-whisper_cpp_small.json'
                status.write_text(json.dumps({'state': state, **extra}), encoding='utf-8')

            fake_hf = ModuleType('huggingface_hub')
            fake_hf.HfApi = lambda **_k: fake_api
            fake_hf.snapshot_download = fake_snapshot
            with patch.dict(catalog.CATALOG, {'whisper_cpp_small': patched_entry}), \
                 patch.dict(sys.modules, {'huggingface_hub': fake_hf}), \
                 patch.dict(os.environ, {'ARANDU_WHISPER_SERVER_BIN': ''}):
                manifest = catalog.install(root, 'whisper_cpp_small', progress=on_progress)
            self.assertEqual(manifest['revision'], rev)
            self.assertTrue(any(st == 'downloading' and 'bytes_downloaded' in ex for st, ex in events))
            self.assertTrue(any(st == 'verifying' for st, _ in events))
            self.assertFalse((root / '.install-whisper_cpp_small.json').exists())


class WhisperCppBackendUnitTests(unittest.TestCase):
    def test_fixed_portuguese_rejected_if_overridden(self):
        for bad_lang in ('en', 'auto', 'pt-PT', '', None):
            with self.assertRaisesRegex(ValueError, 'Idioma fixo'):
                validate_fixed_portuguese(bad_lang, False, False)
        with self.assertRaisesRegex(ValueError, 'Tradução automática'):
            validate_fixed_portuguese('pt', True, False)
        with self.assertRaisesRegex(ValueError, 'Detecção automática'):
            validate_fixed_portuguese('pt', False, True)
        self.assertEqual(validate_fixed_portuguese('pt', False, False), 'pt')

    def test_missing_binary_reports_runtime_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'ARANDU_WHISPER_SERVER_BIN': ''}):
            model_dir = Path(tmp)
            (model_dir / 'ggml-small.bin').write_bytes(b'\x00' * 4096)
            backend = WhisperCppBackend(model_dir, threads=2, model_id='whisper_cpp_small')
            with self.assertRaises(AranduRuntimeUnavailableError):
                backend.load()
            ok, note = catalog._runtime_status('whisper_cpp_small')
            self.assertFalse(ok)
            self.assertIn('MODEL_RUNTIME_UNAVAILABLE', note)

    def test_corrupt_and_lfs_pointer_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp)
            p = model_dir / 'ggml-small.bin'
            p.write_bytes(b'short')
            with self.assertRaisesRegex(ValueError, 'incompleto'):
                WhisperCppBackend(model_dir, model_id='whisper_cpp_small').load()
            p.write_bytes(b'version https://git-lfs.github.com/spec/v1\n' + b'x' * 2048)
            with self.assertRaisesRegex(ValueError, 'Git LFS'):
                WhisperCppBackend(model_dir, model_id='whisper_cpp_small').load()

    def test_persistent_server_reused_and_fixed_pt_args_sent(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            bin_path = _create_fake_whisper_server(d, mode='ok')
            (d / 'ggml-small.bin').write_bytes(b'GGML' + b'\x00' * 4096)
            with patch.dict(os.environ, {'ARANDU_WHISPER_SERVER_BIN': str(bin_path)}):
                backend = backends.create_backend('whisper_cpp_small', d, threads=2)
                self.assertIsInstance(backend, WhisperCppBackend)
                backend.load()
                try:
                    pid_first = backend.server_pid
                    self.assertIsNotNone(pid_first)
                    self.assertGreater(backend.load_ms, 0.0)
                    # Silence never hits server
                    self.assertEqual(backend.recognize(np.zeros(1600, dtype=np.float32)), ('', 0.0))
                    audio = np.sin(np.linspace(0, 10, 1600, dtype=np.float32))
                    for _ in range(3):
                        text, elapsed_ms = backend.recognize(audio)
                        self.assertEqual(text, 'liga a luz da sala')
                        self.assertGreater(elapsed_ms, 0.0)
                        self.assertEqual(backend.server_pid, pid_first)
                    with self.assertRaises(ValueError):
                        backend.recognize(audio, language='en')
                    with self.assertRaises(ValueError):
                        backend.recognize(audio, translate=True)
                finally:
                    backend.close()
                self.assertIsNone(backend.server_pid)
                args = json.loads((d / 'server_args.json').read_text(encoding='utf-8'))
                self.assertIn('--language', args)
                self.assertEqual(args[args.index('--language') + 1], 'pt')
                self.assertIn('--max-context', args)
                self.assertIn('--no-gpu', args)
                self.assertNotIn('--no-context', args)
                calls = [json.loads(line) for line in (d / 'server_calls.jsonl').read_text(encoding='utf-8').splitlines()]
                self.assertEqual(len(calls), 3)
                self.assertTrue(all(c['has_pt'] and c['no_translate'] for c in calls))

    def test_timeout_and_oom_differentiation(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / 'ggml-medium.bin').write_bytes(b'GGML' + b'\x00' * 4096)
            slow_bin = _create_fake_whisper_server(d, mode='slow')
            with patch.dict(os.environ, {'ARANDU_WHISPER_SERVER_BIN': str(slow_bin)}):
                b = WhisperCppBackend(d, threads=2, model_id='whisper_cpp_medium', request_timeout_sec=0.15).load()
                try:
                    with self.assertRaises(TimeoutError):
                        b.recognize(np.ones(800, dtype=np.float32) * 0.1)
                finally:
                    b.close()
            oom_bin = _create_fake_whisper_server(d, mode='oom_on_load')
            with patch.dict(os.environ, {'ARANDU_WHISPER_SERVER_BIN': str(oom_bin)}):
                with self.assertRaises(MemoryError):
                    WhisperCppBackend(d, threads=2, model_id='whisper_cpp_medium').load()


class SttProcessingAndLabBenchmarkTests(unittest.IsolatedAsyncioTestCase):
    def test_edit_operations_and_precision_not_measurable_without_reference(self):
        sub, ins, dele = edit_operations(['liga', 'a', 'luz'], ['liga', 'o', 'abajur', 'agora'])
        self.assertEqual(sub + ins + dele, distance(['liga', 'a', 'luz'], ['liga', 'o', 'abajur', 'agora']))
        empty = evaluate('', 'liga a luz', 'liga a luz')
        self.assertFalse(empty['evaluated'])
        self.assertEqual(empty['precision_status'], 'Precisão não mensurável')

        scored = evaluate('liga a luz da sala', 'liga luz da varanda', 'liga a luz da sala')
        self.assertTrue(scored['evaluated'])
        self.assertEqual(scored['precision_status'], 'Avaliado')
        self.assertFalse(scored['exact_match_raw'])
        self.assertTrue(scored['exact_match_final'])
        self.assertGreater(scored['wer_raw'], 0.0)
        self.assertEqual(scored['wer_final'], 0.0)
        self.assertEqual(scored['substitutions_final'], 0)
        self.assertEqual(scored['insertions_final'], 0)
        self.assertEqual(scored['deletions_final'], 0)
        self.assertGreater(scored['substitutions_raw'] + scored['insertions_raw'] + scored['deletions_raw'], 0)

    def test_lab_worker_warm_repetitions_stats_and_child_rss(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inv = root / 'arandu_ha_inventory.json'
            inv.write_text('{"entities": []}', encoding='utf-8')
            fake_engine = Mock()
            fake_engine.load_ms = 210.0
            fake_engine.load = Mock(return_value=fake_engine)
            fake_engine.recognize = Mock(side_effect=[
                ('liga a luz', 95.0),  # warmup
                ('liga a luz', 40.0),
                ('liga a luz', 50.0),
                ('liga a luz', 60.0),
            ])
            child_proc = SimpleNamespace(memory_info=lambda: SimpleNamespace(rss=200 * 1048576))
            parent_proc = SimpleNamespace(memory_info=lambda: SimpleNamespace(rss=50 * 1048576), children=lambda recursive=True: [child_proc])
            body = {
                'root': str(root),
                'command': 'test',
                'model': 'whisper_cpp_small',
                'variant': 'adapted',
                'threads': 2,
                'repetitions': 3,
                'warmup': True,
                'reference': 'liga a luz',
                'inventory': str(inv),
                'pcm': base64.b64encode((np.sin(np.linspace(0, 10, 3200)) * 5000).astype('<i2').tobytes()).decode(),
            }
            fake_resolver = SimpleNamespace(apply=lambda raw, profile: {'text': raw, 'changes': []})
            with patch('lab_worker.verify', return_value={'revision': catalog.CATALOG['whisper_cpp_small']['revision'], 'sha256': {}}), \
                 patch('backends.create_backend', return_value=fake_engine), \
                 patch('contextual_lexical.ContextualResolver', return_value=fake_resolver), \
                 patch('psutil.Process', return_value=parent_proc):
                result = lab_worker.run(body)

            fake_engine.load.assert_called_once()
            self.assertEqual(fake_engine.recognize.call_count, 4)
            fake_engine.close.assert_called_once()
            self.assertEqual(result['repetitions'], 3)
            self.assertEqual(result['warmup_ms'], 95.0)
            self.assertEqual(result['inference_ms'], 50.0)
            self.assertEqual(result['inference_stats']['min_ms'], 40.0)
            self.assertEqual(result['inference_stats']['p50_ms'], 50.0)
            self.assertEqual(result['inference_stats']['mean_ms'], 50.0)
            self.assertEqual(result['inference_stats']['max_ms'], 60.0)
            self.assertAlmostEqual(result['process_rss_mb'], 250.0, places=1)
            self.assertEqual(result['language_label'], 'Português — fixo')
            self.assertTrue(result['language_fixed'])
            self.assertEqual(result['parameters']['language'], 'pt')

    async def test_stt_lab_cancel_compare_error_isolation_and_production_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            manager = SimpleNamespace(
                data=data,
                processes={},
                failures={},
                config={'stt': {'stt_model': 'fastconformer', 'model_variant': 'adapted', 'threads': 2}},
                restart=AsyncMock(),
            )
            lab = STTLab(manager)
            with self.assertRaisesRegex(ValueError, 'Idioma fixo'):
                lab.parameters({'model': 'whisper_cpp_small', 'language': 'en'})
            with self.assertRaisesRegex(ValueError, 'Repetições inválidas'):
                lab.parameters({'model': 'whisper_cpp_small', 'repetitions': 7})

            async def fake_worker(payload, timeout=90):
                m = payload['model']
                if m == 'whisper_cpp_small':
                    await lab.cancel()
                    raise WorkerExecutionError('Pesos ausentes', error_code='not_installed')
                return {'model': m, 'state': 'ready', 'inference_ms': 10.0, 'model_load_ms': 5.0, 'lexical_ms': 1.0, 'quality': {'evaluated': False}}

            lab.worker = fake_worker
            out = await lab.test(
                {
                    'audio': base64.b64encode(_wav_bytes()).decode(),
                    'models': ['whisper_cpp_small', 'whisper_cpp_medium'],
                    'repetitions': 3,
                },
                compare=True,
            )
            self.assertTrue(out['cancelled'])
            self.assertEqual(out['results'][0]['error_code'], 'cancelled')
            self.assertEqual(out['results'][1]['state'], 'cancelled')
            manager.restart.assert_not_awaited()

    async def test_ha_ai_lab_accepts_14_models_and_cancel_while_locked(self):
        from custom_components.local_nlu import ai_lab

        ha = ModuleType('homeassistant.helpers.aiohttp_client')
        ha.async_get_clientsession = lambda _hass: object()
        entry = SimpleNamespace(entry_id='fixture', options={'stack_endpoint': 'http://local-arandu-stack:8099'}, data={})
        hass = SimpleNamespace(config_entries=SimpleNamespace(async_entries=lambda _d: [entry]))
        lab = ai_lab.Lab(hass)
        admin = SimpleNamespace(is_active=True, is_admin=True)
        await lab.lock.acquire()
        try:
            with patch.dict(sys.modules, {'homeassistant.helpers.aiohttp_client': ha}), \
                 patch.object(ai_lab, 'local_request', AsyncMock(return_value={'cancelled': True})) as req:
                res = await lab.handle(admin, {'command': 'stt_cancel'})
            self.assertTrue(res['cancelled'])
            self.assertEqual(req.call_args.args[1:3], ('http://local-arandu-stack:8099', '/api/stt/cancel'))
        finally:
            lab.lock.release()

        with patch.dict(sys.modules, {'homeassistant.helpers.aiohttp_client': ha}):
            with self.assertRaisesRegex(ValueError, 'Idioma fixo'):
                await lab.handle(admin, {
                    'command': 'stt_test',
                    'stt_model': 'whisper_cpp_small',
                    'language': 'en',
                    'audio': base64.b64encode(_wav_bytes()).decode(),
                })
