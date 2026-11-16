from __future__ import annotations

import asyncio
import base64
import io
import json
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'arandu_stack'))
sys.path.insert(0, str(ROOT / 'arandu_stack' / 'app'))
sys.path.insert(0, str(ROOT / 'arandu_stack' / 'rootfs' / 'modules' / 'arandu_stt' / 'rootfs' / 'src'))
sys.path.insert(0, str(ROOT / 'arandu_stack' / 'rootfs' / 'modules' / 'arandu_stt' / 'rootfs' / 'src' / 'arandu_stt'))


def _make_pcm_b64(duration_s: float = 0.25, sample_rate: int = 16000) -> str:
    return base64.b64encode(b'\x20\x00' * int(duration_s * sample_rate)).decode('ascii')


class MemoryBudgetAndOomDiagnosticsTests(unittest.TestCase):
    def test_compute_memory_budget_policies_auto_custom_none(self):
        from arandu_stt.memory_budget import compute_memory_budget

        host = {'host_total_mb': 16000.0, 'host_available_mb': 11000.0}
        cgroup = {'cgroup_limit_mb': None, 'cgroup_usage_mb': None, 'cgroup_oom_events': None}
        with patch('arandu_stt.memory_budget.read_host_memory_info', return_value=host), \
             patch('arandu_stt.memory_budget.read_cgroup_memory_info', return_value=cgroup):
            auto_b = compute_memory_budget(policy='auto', estimated_ram_mb=3500.0, production_stt_rss_mb=500.0)
            self.assertEqual(auto_b['memory_policy'], 'auto')
            self.assertAlmostEqual(auto_b['effective_available_mb'], 11000.0, places=1)
            self.assertEqual(auto_b['memory_limit_mb'], 8800)
            self.assertTrue(auto_b['sufficient_for_load'])

            custom_b = compute_memory_budget(policy='custom', custom_limit_mb=6144, estimated_ram_mb=3500.0, production_stt_rss_mb=500.0)
            self.assertEqual(custom_b['memory_policy'], 'custom')
            self.assertEqual(custom_b['memory_limit_mb'], 6144)
            self.assertTrue(custom_b['sufficient_for_load'])

            none_b = compute_memory_budget(policy='none', estimated_ram_mb=3500.0, production_stt_rss_mb=500.0)
            self.assertEqual(none_b['memory_policy'], 'none')
            self.assertIsNone(none_b['memory_limit_mb'])
            self.assertTrue(none_b['sufficient_for_load'])

    def test_auto_blocks_insufficient_ram_and_respects_cgroup_headroom(self):
        from arandu_stt.memory_budget import compute_memory_budget

        host = {'host_total_mb': 16000.0, 'host_available_mb': 10000.0}
        cgroup = {'cgroup_limit_mb': 4096.0, 'cgroup_usage_mb': 3500.0, 'cgroup_oom_events': None}
        with patch('arandu_stt.memory_budget.read_host_memory_info', return_value=host), \
             patch('arandu_stt.memory_budget.read_cgroup_memory_info', return_value=cgroup):
            budget = compute_memory_budget(policy='auto', estimated_ram_mb=3500.0)
            self.assertFalse(budget['sufficient_for_load'])
            self.assertIsNotNone(budget['insufficient_reason'])
            self.assertAlmostEqual(budget['effective_available_mb'], 596.0, places=1)

    def test_classify_memory_failure_distinguishes_all_five_origins(self):
        from arandu_stt.memory_budget import classify_memory_failure

        with patch('arandu_stt.memory_budget.read_cgroup_memory_info', return_value={'cgroup_limit_mb': None, 'cgroup_usage_mb': None, 'cgroup_oom_detected': False}):
            d1 = classify_memory_failure(exc=RuntimeError('RSS excedeu limite interno de 4608 MiB'), stage='load', internal_limit_mb=4608, observed_rss_mb=4700.0)
            self.assertEqual(d1['failure_origin'], 'internal_limit_exceeded')
            self.assertEqual(d1['stage'], 'load')

            d3 = classify_memory_failure(exc=RuntimeError('worker killed by signal 9'), returncode=-9, stage='infer')
            self.assertEqual(d3['failure_origin'], 'kernel_oom')

            d4 = classify_memory_failure(exc=RuntimeError('DefaultCPUAllocator: not enough memory'), stage='infer')
            self.assertEqual(d4['failure_origin'], 'runtime_alloc_error')

            d5 = classify_memory_failure(exc=RuntimeError('Corrupted onnx file header'), stage='load')
            self.assertEqual(d5['failure_origin'], 'model_load_failed')

        with patch('arandu_stt.memory_budget.read_cgroup_memory_info', return_value={'cgroup_limit_mb': 4096.0, 'cgroup_usage_mb': 4095.0, 'cgroup_oom_detected': True}):
            d2 = classify_memory_failure(exc=RuntimeError('worker terminated'), returncode=-9, stage='load')
            self.assertEqual(d2['failure_origin'], 'cgroup_limit_exceeded')


class WhisperCppQuantizedAndAvxTests(unittest.TestCase):
    def test_quantized_models_catalog_and_backends_mapping(self):
        import backends
        import model_catalog
        import whisper_cpp_backend

        for mid in (
            'whisper_cpp_small',
            'whisper_cpp_small_q8_0',
            'whisper_cpp_small_q5_1',
            'whisper_cpp_medium',
            'whisper_cpp_medium_q8_0',
            'whisper_cpp_medium_q5_0',
        ):
            self.assertIn(mid, model_catalog.CATALOG)
            self.assertIn(mid, whisper_cpp_backend.MODEL_FILENAMES)
            self.assertEqual(model_catalog.CATALOG[mid]['runtime'], 'whisper.cpp')
            first_sha = next(iter(model_catalog.CATALOG[mid]['sha256'].values()))
            self.assertEqual(len(first_sha), 64)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(whisper_cpp_backend, 'WhisperCppBackend') as cls:
                backends.create_backend('whisper_cpp_small_q5_1', root, threads=4)
                cls.assert_called_once_with(root, 4, model_id='whisper_cpp_small_q5_1')

    def test_cpu_avx2_detection_selects_portable_when_unsupported(self):
        from arandu_stt import whisper_cpp_backend

        with tempfile.TemporaryDirectory() as tmp:
            avx_bin = Path(tmp) / 'whisper-server'
            port_bin = Path(tmp) / 'whisper-server-portable'
            avx_bin.write_bytes(b'bin')
            port_bin.write_bytes(b'bin')
            with patch.object(whisper_cpp_backend, 'DEFAULT_WHISPER_SERVER_PATHS', (avx_bin,)), \
                 patch.object(whisper_cpp_backend, 'PORTABLE_WHISPER_SERVER_PATHS', (port_bin,)), \
                 patch.dict('os.environ', {}, clear=False):
                with patch.object(whisper_cpp_backend, '_cpu_supports_avx2_fma', return_value=False):
                    self.assertEqual(whisper_cpp_backend.find_whisper_server_binary(), str(port_bin))
                with patch.object(whisper_cpp_backend, '_cpu_supports_avx2_fma', return_value=True):
                    self.assertEqual(whisper_cpp_backend.find_whisper_server_binary(), str(avx_bin))


class LabWorkerPersistentServeTests(unittest.TestCase):
    def test_persistent_serve_reuses_resident_model_separates_cold_warm_and_unloads(self):
        from arandu_stt import lab_worker

        load_calls = []

        class FakeEngine:
            def __init__(self, model):
                self.model = model
                self.closed = False
                self.calls = 0
                self.load_ms = 15.0

            def load(self):
                load_calls.append(self.model)

            def recognize(self, pcm):
                self.calls += 1
                return f'ligar luz {self.calls}', 14.5

            def close(self):
                self.closed = True

            def process_rss_mb(self):
                return 420.5

        created = []

        def fake_create(model, directory, threads=2, **kwargs):
            eng = FakeEngine(model)
            created.append(eng)
            return eng

        pcm_b64 = _make_pcm_b64(0.3)
        tmp_dir = tempfile.gettempdir()
        inv_path = Path(tmp_dir) / 'test_inventory_persist.json'
        inv_path.write_text('{"entities": []}', encoding='utf-8')
        commands = [
            {'cmd': 'load', 'model': 'orukeet_int8', 'variant': 'int8', 'threads': 2, 'root': tmp_dir, 'warmup': True, 'pcm': pcm_b64, 'inventory': str(inv_path)},
            {'cmd': 'test', 'model': 'orukeet_int8', 'variant': 'int8', 'threads': 2, 'root': tmp_dir, 'execution_mode': 'warm', 'resident': True, 'repetitions': 3, 'pcm': pcm_b64, 'inventory': str(inv_path)},
            {'cmd': 'test', 'model': 'orukeet_int8', 'variant': 'int8', 'threads': 2, 'root': tmp_dir, 'execution_mode': 'warm', 'resident': True, 'repetitions': 1, 'pcm': pcm_b64, 'inventory': str(inv_path)},
            {'cmd': 'test', 'model': 'orukeet_int8', 'variant': 'int8', 'threads': 2, 'root': tmp_dir, 'execution_mode': 'warm', 'resident': True, 'repetitions': 1, 'pcm': pcm_b64, 'inventory': str(inv_path)},
            {'cmd': 'unload'},
            {'cmd': 'shutdown'},
        ]
        stdin = io.StringIO('\n'.join(json.dumps(c) for c in commands) + '\n')
        stdout = io.StringIO()

        with patch('backends.create_backend', side_effect=fake_create), \
             patch.object(lab_worker, 'verify', return_value={'revision': 'test', 'inventory': {}}), \
             patch.object(lab_worker, 'location', return_value=Path(tmp_dir)), \
             patch('sys.stdin', stdin), patch('sys.stdout', stdout):
            rc = lab_worker._serve_main()

        self.assertEqual(rc, 0)
        lines = [json.loads(line) for line in stdout.getvalue().strip().splitlines()]
        self.assertEqual(len(lines), 6)
        self.assertEqual(len(load_calls), 1)
        self.assertTrue(lines[0]['ok'])
        self.assertIn(lines[0]['state'], ('resident', 'ready'))

        for idx in (1, 2, 3):
            res = lines[idx]
            self.assertTrue(res['ok'], res)
            self.assertTrue(res['session_reused'])
            self.assertEqual(res['cold_load_ms'], 0.0)
            self.assertEqual(res['model_load_ms'], 0.0)
            self.assertIn('ram_initial_mb', res)
            self.assertIn('ram_peak_mb', res)
            self.assertIn('ram_final_mb', res)
            self.assertIn('cpu_time_ms', res)
            self.assertIn('cpu_percent', res)
            self.assertGreaterEqual(res['total_ms'], res['inference_ms'])

        self.assertTrue(lines[4]['ok'])
        self.assertEqual(lines[4]['state'], 'unloaded')
        self.assertTrue(created[0].closed)

    def test_cold_mode_vs_warmup_benchmark_mode(self):
        from arandu_stt import lab_worker

        created = []

        class FakeEngine:
            def __init__(self):
                self.load_ms = 12.5

            def load(self):
                pass

            def recognize(self, pcm):
                return 'ok', 9.5

            def close(self):
                pass

        pcm_b64 = _make_pcm_b64(0.25)
        tmp_dir = tempfile.gettempdir()
        inv_path = Path(tmp_dir) / 'test_inventory_cold_warm.json'
        inv_path.write_text('{"entities": []}', encoding='utf-8')
        commands = [
            {'cmd': 'test', 'model': 'whisper_cpp_small_q8_0', 'variant': 'int8', 'threads': 4, 'root': tmp_dir, 'execution_mode': 'cold', 'resident': True, 'repetitions': 1, 'pcm': pcm_b64, 'inventory': str(inv_path)},
            {'cmd': 'test', 'model': 'whisper_cpp_small_q8_0', 'variant': 'int8', 'threads': 4, 'root': tmp_dir, 'execution_mode': 'warmup_benchmark', 'resident': True, 'repetitions': 3, 'pcm': pcm_b64, 'inventory': str(inv_path)},
            {'cmd': 'shutdown'},
        ]
        stdin = io.StringIO('\n'.join(json.dumps(c) for c in commands) + '\n')
        stdout = io.StringIO()

        def fake_create(*args, **kwargs):
            eng = FakeEngine()
            created.append(eng)
            return eng

        with patch('backends.create_backend', side_effect=fake_create), \
             patch.object(lab_worker, 'verify', return_value={'revision': 'test', 'inventory': {}}), \
             patch.object(lab_worker, 'location', return_value=Path(tmp_dir)), \
             patch('sys.stdin', stdin), patch('sys.stdout', stdout):
            lab_worker._serve_main()

        lines = [json.loads(line) for line in stdout.getvalue().strip().splitlines()]
        cold_res = lines[0]
        warm_bench_res = lines[1]
        self.assertTrue(cold_res['ok'], cold_res)
        self.assertTrue(warm_bench_res['ok'], warm_bench_res)
        self.assertFalse(cold_res['session_reused'])
        self.assertGreater(cold_res['cold_load_ms'], 0.0)
        self.assertTrue(warm_bench_res['session_reused'])
        self.assertGreater(warm_bench_res['warmup_ms'], 0.0)


class SttLabSessionManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_session_manager_crash_recovery_cancel_lock_and_prod_isolation(self):
        from app import stt_lab

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = SimpleNamespace(
                data=root,
                config={'stt': {'stt_model': 'fastconformer', 'model_variant': 'int8', 'threads': 2}},
                processes={},
                failures={},
                restart=AsyncMock(),
            )
            lab = stt_lab.STTLab(manager)
            lab.active = AsyncMock(return_value=None)

            # Simulate crash on first persistent command
            proc_crashed = MagicMock()
            proc_crashed.returncode = -9
            proc_crashed.stdin = MagicMock()
            proc_crashed.stdout = MagicMock()
            proc_crashed.stderr = MagicMock()
            proc_crashed.stderr.read = AsyncMock(return_value=b'Killed')
            proc_crashed.wait = AsyncMock(return_value=-9)
            lab._session_proc = proc_crashed
            lab.session_state = 'resident'
            lab.session_info = {'model': 'nemotron_unified_0_6b'}

            with patch('arandu_stt.memory_budget.read_cgroup_memory_info', return_value={'cgroup_limit_mb': None, 'cgroup_usage_mb': None, 'cgroup_oom_detected': False}):
                status = await lab.session_status()
            self.assertEqual(status['state'], 'error')
            self.assertEqual(status['failure_detail']['failure_origin'], 'kernel_oom')

            # Cancel while running clears session cleanly without touching production STT
            await lab.cancel()
            self.assertTrue(lab.cancel_requested)
            manager.restart.assert_not_awaited()

    async def test_ai_lab_ws_and_http_session_endpoints(self):
        from custom_components.local_nlu import ai_lab

        ha = ModuleType('homeassistant.helpers.aiohttp_client')
        ha.async_get_clientsession = lambda _hass: object()
        entry = SimpleNamespace(entry_id='fixture', options={'stack_endpoint': 'http://local-arandu-stack:8099'}, data={})
        hass = SimpleNamespace(config_entries=SimpleNamespace(async_entries=lambda _d: [entry]))
        lab = ai_lab.Lab(hass)
        admin = SimpleNamespace(is_active=True, is_admin=True)

        with patch.dict(sys.modules, {'homeassistant.helpers.aiohttp_client': ha}), \
             patch.object(ai_lab, 'local_request', AsyncMock(return_value={'state': 'resident', 'model': 'orukeet_int8'})) as req:
            s = await lab.handle(admin, {'command': 'stt_session', 'memory_policy': 'auto'})
            self.assertEqual(s['state'], 'resident')
            l = await lab.handle(admin, {'command': 'stt_load', 'stt_model': 'whisper_cpp_small_q8_0', 'memory_policy': 'none'})
            self.assertEqual(l['state'], 'resident')
            w = await lab.handle(admin, {'command': 'stt_warmup'})
            self.assertEqual(w['state'], 'resident')
            u = await lab.handle(admin, {'command': 'stt_unload'})
            self.assertEqual(u['state'], 'resident')
            self.assertEqual(req.await_count, 4)


class ProductionSttErrorDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_wyoming_handler_distinguishes_stt_busy_and_exposes_stage_on_stt_failure(self):
        import server as stt_server
        from wyoming.audio import AudioChunk, AudioStart, AudioStop

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = stt_server.Engine(root, 2, 'fastconformer', 'adapted', resident_policy='LAZY')
            engine.backend = MagicMock()
            resolver = MagicMock()
            metrics = stt_server.Metrics(root / 'metrics.jsonl')

            async def run_utterance():
                handler = stt_server.Handler(engine, resolver, metrics, 0, 'full', reader=AsyncMock(), writer=AsyncMock())
                sent = []
                handler.write_event = AsyncMock(side_effect=lambda ev: sent.append(ev))
                await handler.handle_event(AudioStart(rate=16000, width=2, channels=1).event())
                await handler.handle_event(AudioChunk(rate=16000, width=2, channels=1, audio=b'\x20\x00' * 8000).event())
                await handler.handle_event(AudioStop().event())
                return sent

            # 1) Busy lock -> stt_busy (not stt_failure)
            engine._lock.acquire()
            try:
                sent_busy = await run_utterance()
            finally:
                engine._lock.release()
            self.assertEqual(len(sent_busy), 1)
            self.assertEqual(sent_busy[0].data['code'], 'stt_busy')
            self.assertIn('STT ocupado', sent_busy[0].data['text'])

            # 2) Unexpected backend failure -> stt_failure with stage + exception + metadata
            engine.backend.recognize = MagicMock(side_effect=RuntimeError('whisper-server HTTP 500'))
            sent_fail = await run_utterance()
            self.assertEqual(len(sent_fail), 1)
            self.assertEqual(sent_fail[0].data['code'], 'stt_failure')
            self.assertIn('[recognize]', sent_fail[0].data['text'])
            self.assertIn('RuntimeError: whisper-server HTTP 500', sent_fail[0].data['text'])
            meta = engine.metadata()
            self.assertEqual(meta['last_infer_error']['stage'], 'recognize')
            self.assertEqual(meta['last_infer_error']['error_type'], 'RuntimeError')


if __name__ == '__main__':
    unittest.main()
