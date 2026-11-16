"""Comprehensive regression tests for STT runtime detection and NVIDIA Nemotron Unified ASR 0.6B."""
from __future__ import annotations

import asyncio
import builtins
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / 'arandu_stack' / 'app'
STT_SRC = ROOT / 'arandu_stack' / 'rootfs' / 'modules' / 'arandu_stt' / 'rootfs' / 'src' / 'arandu_stt'
for p in (str(ROOT), str(APP_DIR), str(STT_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

import model_catalog  # noqa: E402
import nemotron_unified_backend as nemotron_mod  # noqa: E402
from stt_errors import AranduStructuredError  # noqa: E402
from stt_lab import STTLab, WorkerExecutionError  # noqa: E402


def _make_python_wrapper(tmp_dir: Path, name: str, env_overrides: dict[str, str]) -> Path:
    """Create a real executable interpreter wrapper (POSIX shell or Windows .cmd) invoking sys.executable."""
    if os.name == 'nt':
        script = tmp_dir / f'{name}.cmd'
        lines = ['@echo off', 'setlocal']
        for k, v in env_overrides.items():
            lines.append(f'set "{k}={v}"')
        lines.append(f'"{sys.executable}" %*')
        script.write_text('\r\n'.join(lines) + '\r\n', encoding='utf-8')
        return script
    script = tmp_dir / f'{name}.sh'
    lines = ['#!/usr/bin/env sh']
    for k, v in env_overrides.items():
        escaped = v.replace("'", "'\"'\"'")
        lines.append(f"export {k}='{escaped}'")
    lines.append(f'exec "{sys.executable}" "$@"')
    script.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    script.chmod(0o755)
    return script


def _make_executable_stub(path: Path) -> Path:
    path.write_text('#!/usr/bin/env sh\nexit 0\n', encoding='utf-8')
    if os.name != 'nt':
        path.chmod(0o755)
    return path


def _make_fake_stt_site(base: Path) -> Path:
    """Create lightweight stub modules for all standard STT / Phonon / Nemotron runtimes on PYTHONPATH."""
    site = base / 'fake_site'
    site.mkdir(parents=True, exist_ok=True)
    for simple_mod in (
        'sherpa_onnx',
        'faster_whisper',
        'ctranslate2',
        'onnx_asr',
        'onnxruntime',
        'transcribe_cpp',
        'soundfile',
        'torch',
    ):
        (site / f'{simple_mod}.py').write_text(
            textwrap.dedent(
                """
                def set_num_threads(_n):
                    pass

                def set_grad_enabled(_flag):
                    pass
                """
            ).strip()
            + '\n',
            encoding='utf-8',
        )
    if (site / 'numpy.py').exists():
        (site / 'numpy.py').unlink()
    # Copy lightweight fermion stub
    (site / 'fermion.py').write_text('def load_speech(*_a, **_kw):\n    return None\n', encoding='utf-8')
    # Package nemo.collections.asr.models.ASRModel
    nemo_dir = site / 'nemo' / 'collections' / 'asr'
    nemo_dir.mkdir(parents=True, exist_ok=True)
    (site / 'nemo' / '__init__.py').write_text('', encoding='utf-8')
    (site / 'nemo' / 'collections' / '__init__.py').write_text('', encoding='utf-8')
    (nemo_dir / '__init__.py').write_text(
        'from nemo.collections.asr import models\n',
        encoding='utf-8',
    )
    (nemo_dir / 'models.py').write_text(
        textwrap.dedent(
            """
            import json
            import os
            import time
            from pathlib import Path


            class _DummyDefaults(dict):
                def get(self, key, default=None):
                    return super().get(key, default)


            class _DummyCfg:
                def __init__(self):
                    self.model_defaults = _DummyDefaults(
                        prompt_dictionary={'pt': 1, 'pt-BR': 1, 'en-US': 2, 'auto': 0}
                    )

                def __str__(self):
                    return 'unified_noncausal_fastconformer_rnnt'


            class _DummyHypothesis:
                def __init__(self, text):
                    self.text = text


            class ASRModel:
                @classmethod
                def restore_from(cls, restore_path, map_location='cpu'):
                    if os.environ.get('FAKE_NEMO_SLOW_LOAD'):
                        time.sleep(float(os.environ['FAKE_NEMO_SLOW_LOAD']))
                    if os.environ.get('FAKE_NEMO_OOM_LOAD'):
                        raise MemoryError('Out of memory loading checkpoint')
                    if os.environ.get('FAKE_NEMO_CRASH_LOAD'):
                        import sys
                        sys.stderr.write('Fatal crash during load\\n')
                        os._exit(1)
                    if os.environ.get('FAKE_NEMO_SIGKILL_LOAD'):
                        os._exit(137)
                    inst = cls()
                    inst.restore_path = restore_path
                    inst.map_location = map_location
                    inst.cfg = _DummyCfg()
                    inst.prompt_lang = None
                    inst.log_path = os.environ.get('FAKE_NEMO_LOG_FILE')
                    return inst

                def eval(self):
                    return self

                def freeze(self):
                    return self

                def set_inference_prompt(self, lang):
                    self.prompt_lang = lang

                def transcribe(self, paths, batch_size=1, num_workers=0, verbose=False, target_lang='pt'):
                    if os.environ.get('FAKE_NEMO_SLOW_INFER'):
                        time.sleep(float(os.environ['FAKE_NEMO_SLOW_INFER']))
                    if os.environ.get('FAKE_NEMO_CRASH_INFER'):
                        import sys
                        sys.stderr.write('Runtime aborted unexpectedly\\n')
                        os._exit(2)
                    if os.environ.get('FAKE_NEMO_OOM_INFER'):
                        import sys
                        sys.stderr.write('CUDA/CPU OutOfMemory error\\n')
                        os._exit(137)
                    if self.log_path:
                        with open(self.log_path, 'a', encoding='utf-8') as fh:
                            fh.write(json.dumps({
                                'paths': list(paths),
                                'target_lang': target_lang,
                                'prompt_lang': self.prompt_lang,
                                'weights_only_disabled': os.environ.get('TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD'),
                            }) + '\\n')
                    return [_DummyHypothesis('ligar a luz da sala')]
            """
        ).strip()
        + '\n',
        encoding='utf-8',
    )
    return site


class _DummyManager:
    def __init__(self, data_dir: Path, stt_model: str = 'fastconformer'):
        self.data = data_dir
        self.data.mkdir(parents=True, exist_ok=True)
        self.processes = {}
        self.failures = {}
        self.config = {
            'stt': {
                'enabled': True,
                'stt_model': stt_model,
                'model_variant': 'adapted',
                'threads': 2,
                'profile': 'full',
                'whisper_profile': 'normalize_only',
                'resident_policy': 'LAZY',
                'lexical_enabled': True,
                'closed_vocabulary': True,
                'generic_weight': 7,
                'home_assistant_weight': 10,
            }
        }
        self.saved = []
        self.configured = []
        self.stopped = []
        self.started = []

    def save(self):
        self.saved.append(dict(self.config['stt']))

    async def configure(self, module, values):
        self.configured.append((module, dict(values)))
        self.config[module].update(values)

    async def stop(self, module, persist=True):
        self.stopped.append((module, persist))

    async def start(self, module, persist=True):
        self.started.append((module, persist))


@pytest.fixture(autouse=True)
def _clear_runtime_cache():
    model_catalog.invalidate_runtime_cache()
    yield
    model_catalog.invalidate_runtime_cache()


# ---------------------------------------------------------------------------
# 1 & 2. Global Python without numpy & /opt/venvs/* subprocess detection
# ---------------------------------------------------------------------------

def test_01_control_plane_without_numpy_does_not_fail_models_or_describe(tmp_path, monkeypatch):
    site = _make_fake_stt_site(tmp_path)
    py_env = {'PYTHONPATH': str(site)}
    stt_py = _make_python_wrapper(tmp_path, 'stt_py', py_env)
    phonon_py = _make_python_wrapper(tmp_path, 'phonon_py', py_env)
    nemo_py = _make_python_wrapper(tmp_path, 'nemo_py', py_env)
    whisper_bin = _make_executable_stub(tmp_path / ('whisper-server.cmd' if os.name == 'nt' else 'whisper-server'))
    transcribe_lib = tmp_path / 'libtranscribe.so'
    transcribe_lib.write_text('stub', encoding='utf-8')

    monkeypatch.setenv('ARANDU_STT_PYTHON', str(stt_py))
    monkeypatch.setenv('ARANDU_PHONON_PYTHON', str(phonon_py))
    monkeypatch.setenv('ARANDU_NEMOTRON_PYTHON', str(nemo_py))
    monkeypatch.setenv('ARANDU_WHISPER_SERVER_BIN', str(whisper_bin))
    monkeypatch.setenv('TRANSCRIBE_LIBRARY', str(transcribe_lib))

    # Block importing numpy inside the current (control-plane) Python process!
    real_import = builtins.__import__

    def _no_numpy_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == 'numpy' or name.startswith('numpy.'):
            raise ModuleNotFoundError("No module named 'numpy'")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.delitem(sys.modules, 'numpy', raising=False)
    monkeypatch.setattr(builtins, '__import__', _no_numpy_import)

    # Also verify phonon_backend and whisper_cpp_backend import cleanly without numpy in control plane
    import phonon_backend  # noqa: F401
    import whisper_cpp_backend  # noqa: F401

    lab = STTLab(_DummyManager(tmp_path / 'data'))
    payload = asyncio.run(lab.models())
    rows = {r['id']: r for r in payload['models']}
    assert len(rows) == len(model_catalog.CATALOG)
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "No module named 'numpy'" not in serialized
    for mid, row in rows.items():
        assert row['runtime_status'] == 'runtime_ready', f'{mid}: {row}'
        assert row['runtime_available'] is True, f'{mid}: {row}'
        assert row['runtime_note'] is None, f'{mid}: {row}'


def test_02_stt_venv_numpy_probed_via_subprocess(tmp_path, monkeypatch):
    site = _make_fake_stt_site(tmp_path)
    stt_py = _make_python_wrapper(tmp_path, 'stt_py', {'PYTHONPATH': str(site)})
    monkeypatch.setenv('ARANDU_STT_PYTHON', str(stt_py))

    info = model_catalog.probe_runtime_status('fastconformer', force_refresh=True)
    assert info['available'] is True
    assert info['runtime_status'] == 'runtime_ready'
    assert info['note'] is None
    assert info['interpreter'] == str(stt_py)


# ---------------------------------------------------------------------------
# 3, 4, 5, 6. Nemotron missing / dependency_missing / dependency_conflict / runtime_ready
# ---------------------------------------------------------------------------

def test_03_missing_nemotron_venv_returns_runtime_missing(tmp_path, monkeypatch):
    monkeypatch.setenv('ARANDU_NEMOTRON_PYTHON', str(tmp_path / 'does_not_exist' / 'python'))
    info = model_catalog.probe_runtime_status('nemotron_unified_0_6b', force_refresh=True)
    assert info['available'] is False
    assert info['runtime_status'] == 'runtime_missing'
    assert info['error_code'] == 'MODEL_RUNTIME_UNAVAILABLE'
    assert 'MODEL_RUNTIME_UNAVAILABLE' in (info['note'] or '')


def test_04_incomplete_nemotron_dependencies_returns_dependency_missing(tmp_path, monkeypatch):
    empty_site = tmp_path / 'empty_site'
    empty_site.mkdir()
    # Block site-packages via -S and expose only stdlib + empty_site where nemo is absent
    nemo_py = _make_python_wrapper(tmp_path, 'nemo_missing', {'PYTHONPATH': str(empty_site)})
    # Wrap sys.executable with a tiny shim removing nemo paths from sys.path before running -c
    shim = tmp_path / 'run_no_nemo.py'
    shim.write_text(
        textwrap.dedent(
            """
            import sys
            sys.meta_path.insert(0, type('NoNemo', (), {
                'find_spec': staticmethod(lambda fullname, *_a: (_ for _ in ()).throw(ModuleNotFoundError("No module named 'nemo'")) if fullname.startswith('nemo') else None)
            })())
            code = sys.argv[sys.argv.index('-c') + 1]
            exec(compile(code, '<probe>', 'exec'), {'__name__': '__main__'})
            """
        ).strip()
        + '\n',
        encoding='utf-8',
    )
    if os.name == 'nt':
        wrapper = tmp_path / 'nemo_incomplete.cmd'
        wrapper.write_text(f'@echo off\r\n"{sys.executable}" "{shim}" %*\r\n', encoding='utf-8')
    else:
        wrapper = tmp_path / 'nemo_incomplete.sh'
        wrapper.write_text(f'#!/usr/bin/env sh\nexec "{sys.executable}" "{shim}" "$@"\n', encoding='utf-8')
        wrapper.chmod(0o755)

    monkeypatch.setenv('ARANDU_NEMOTRON_PYTHON', str(wrapper))
    info = model_catalog.probe_runtime_status('nemotron_unified_0_6b', force_refresh=True)
    assert info['available'] is False
    assert info['runtime_status'] == 'dependency_missing'
    assert info['error_code'] == 'MODEL_RUNTIME_UNAVAILABLE'


def test_05_conflicting_nemotron_dependencies_returns_dependency_conflict(tmp_path, monkeypatch):
    bad_site = tmp_path / 'bad_site'
    nemo_dir = bad_site / 'nemo' / 'collections'
    nemo_dir.mkdir(parents=True)
    (bad_site / 'nemo' / '__init__.py').write_text('', encoding='utf-8')
    (nemo_dir / '__init__.py').write_text('', encoding='utf-8')
    # Incompatible API / version conflict during import
    (nemo_dir / 'asr.py').write_text("raise ImportError('cannot import name ASRModel: incompatible transformers version')\n", encoding='utf-8')
    (bad_site / 'soundfile.py').write_text('', encoding='utf-8')
    (bad_site / 'torch.py').write_text('', encoding='utf-8')

    wrapper = _make_python_wrapper(tmp_path, 'nemo_conflict', {'PYTHONPATH': str(bad_site)})
    monkeypatch.setenv('ARANDU_NEMOTRON_PYTHON', str(wrapper))

    info = model_catalog.probe_runtime_status('nemotron_unified_0_6b', force_refresh=True)
    assert info['available'] is False
    assert info['runtime_status'] == 'dependency_conflict'
    assert info['error_code'] == 'MODEL_RUNTIME_UNAVAILABLE'


def test_06_valid_runtime_without_weights_returns_not_installed_not_runtime_unavailable(tmp_path, monkeypatch):
    site = _make_fake_stt_site(tmp_path)
    wrapper = _make_python_wrapper(tmp_path, 'nemo_ok', {'PYTHONPATH': str(site)})
    monkeypatch.setenv('ARANDU_NEMOTRON_PYTHON', str(wrapper))

    lab = STTLab(_DummyManager(tmp_path / 'data'))
    payload = asyncio.run(lab.models())
    nem = next(m for m in payload['models'] if m['id'] == 'nemotron_unified_0_6b')
    assert nem['runtime_status'] == 'runtime_ready'
    assert nem['runtime_available'] is True
    assert nem['runtime_note'] is None
    assert nem['error_code'] is None
    assert nem['weights_status'] == 'model_not_installed'
    assert nem['load_status'] == 'unloaded'
    assert nem['state'] == 'not_installed'
    assert nem['diagnostic_state'] == 'model_not_installed'
    assert nem['ready'] is False
    assert 'MODEL_RUNTIME_UNAVAILABLE' not in json.dumps(nem)


# ---------------------------------------------------------------------------
# 7 & 8. Corrupt vs valid .nemo verification & describe()
# ---------------------------------------------------------------------------

def test_07_corrupted_nemo_size_and_hash_detected(tmp_path, monkeypatch):
    site = _make_fake_stt_site(tmp_path)
    monkeypatch.setenv('ARANDU_NEMOTRON_PYTHON', str(_make_python_wrapper(tmp_path, 'nemo_ok', {'PYTHONPATH': str(site)})))

    root = tmp_path / 'models'
    d = model_catalog.location(root, 'nemotron_unified_0_6b')
    d.mkdir(parents=True)
    ckpt = d / 'nemotron-labs-unified-asr-0.6b.nemo'
    ckpt.write_bytes(b'corrupt-truncated-data')
    (d / 'manifest.json').write_text(
        json.dumps({
            'id': 'nemotron_unified_0_6b',
            'revision': model_catalog.CATALOG['nemotron_unified_0_6b']['revision'],
            'sha256': {'nemotron-labs-unified-asr-0.6b.nemo': 'deadbeef'},
        }),
        encoding='utf-8',
    )

    desc = model_catalog.describe(root, 'nemotron_unified_0_6b')
    assert desc['runtime_status'] == 'runtime_ready'
    assert desc['weights_status'] == 'model_corrupted'
    assert desc['incomplete'] is True
    assert desc['state'] == 'error'
    assert desc['error_code'] == 'MODEL_INTEGRITY_FAILED'

    with pytest.raises(ValueError, match='Tamanho do arquivo inválido|Hash|Integridade'):
        model_catalog.verify(root, 'nemotron_unified_0_6b')


def test_08_valid_nemo_file_passes_verify_and_describe(tmp_path, monkeypatch):
    import hashlib

    site = _make_fake_stt_site(tmp_path)
    monkeypatch.setenv('ARANDU_NEMOTRON_PYTHON', str(_make_python_wrapper(tmp_path, 'nemo_ok', {'PYTHONPATH': str(site)})))

    root = tmp_path / 'models'
    d = model_catalog.location(root, 'nemotron_unified_0_6b')
    d.mkdir(parents=True)
    payload = b'valid-nemo-archive-bytes' * 64
    ckpt = d / 'nemotron-labs-unified-asr-0.6b.nemo'
    ckpt.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    rev = model_catalog.CATALOG['nemotron_unified_0_6b']['revision']
    (d / 'manifest.json').write_text(
        json.dumps({
            'id': 'nemotron_unified_0_6b',
            'revision': rev,
            'sha256': {'nemotron-labs-unified-asr-0.6b.nemo': digest},
            'load_validated': True,
        }),
        encoding='utf-8',
    )

    orig_entry = dict(model_catalog.CATALOG['nemotron_unified_0_6b'])
    monkeypatch.setitem(
        model_catalog.CATALOG['nemotron_unified_0_6b'],
        'file_sizes',
        {'nemotron-labs-unified-asr-0.6b.nemo': len(payload)},
    )
    monkeypatch.setitem(
        model_catalog.CATALOG['nemotron_unified_0_6b'],
        'sha256',
        {'nemotron-labs-unified-asr-0.6b.nemo': digest},
    )
    try:
        verified = model_catalog.verify(root, 'nemotron_unified_0_6b')
        assert verified['revision'] == rev
        desc = model_catalog.describe(root, 'nemotron_unified_0_6b')
        assert desc['installed'] is True
        assert desc['weights_status'] == 'model_ready'
        assert desc['state'] == 'installed'
    finally:
        model_catalog.CATALOG['nemotron_unified_0_6b'].update(orig_entry)


# ---------------------------------------------------------------------------
# 9, 10, 11, 12, 13, 14, 15. Isolated Nemotron worker RPC lifecycle
# ---------------------------------------------------------------------------

def _setup_worker_env(tmp_path: Path, monkeypatch, extra_env: dict[str, str] | None = None) -> tuple[Path, Path]:
    site = _make_fake_stt_site(tmp_path)
    log_file = tmp_path / 'nemo_transcribe.jsonl'
    env = {'PYTHONPATH': str(site), 'FAKE_NEMO_LOG_FILE': str(log_file)}
    if extra_env:
        env.update(extra_env)
    wrapper = _make_python_wrapper(tmp_path, f'nemo_worker_{len(list(tmp_path.iterdir()))}', env)
    monkeypatch.setenv('ARANDU_NEMOTRON_PYTHON', str(wrapper))
    monkeypatch.setenv('ARANDU_NEMOTRON_MIN_BYTES', '1')

    model_dir = tmp_path / 'nemotron_dir'
    model_dir.mkdir(exist_ok=True)
    (model_dir / 'nemotron-labs-unified-asr-0.6b.nemo').write_bytes(b'NEMO_TEST_CKPT' * 128)
    return model_dir, log_file


def test_09_to_13_persistent_worker_health_load_pt_transcribe_silence_and_unload(tmp_path, monkeypatch):
    model_dir, log_file = _setup_worker_env(tmp_path, monkeypatch)
    backend = nemotron_mod.NemotronUnifiedBackend(model_dir, threads=2, streaming_profile='offline')
    try:
        # Before load -> health stopped
        assert backend.health()['status'] == 'stopped'

        # 10. load
        backend.load()
        assert backend.load_status == 'model_ready'
        assert backend.cold_load_ms >= 0.0
        assert backend.process_rss_mb() > 0.0

        # 9. health
        h = backend.health()
        assert h['ok'] is True
        assert h['status'] == 'healthy'
        assert h['model_loaded'] is True
        assert isinstance(h['pid'], int) and h['pid'] > 0

        # 12. silent audio produces empty transcript without invoking NeMo transcribe
        text_sil, ms_sil = backend.recognize(np.zeros(16000, dtype=np.float32))
        assert text_sil == ''
        assert ms_sil == 0.0
        assert not log_file.exists()

        # 11a. offline PT-BR transcription (with real WAV + TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1)
        tone = (np.sin(np.linspace(0, 200, 16000)) * 0.4).astype(np.float32)
        text, inf_ms = backend.recognize(tone, target_lang='pt')
        assert text == 'ligar a luz da sala'
        assert inf_ms > 0.0
        assert backend.last_inference_mode == 'offline'
        entries = [json.loads(line) for line in log_file.read_text(encoding='utf-8').splitlines() if line.strip()]
        assert entries[-1]['target_lang'] == 'pt'
        assert entries[-1]['weights_only_disabled'] == '1'

        # 11b. buffered streaming profile honestly labels buffered_offline & measures real inference_ms
        sess = backend.start_stream(profile='balanced', target_lang='pt')
        assert sess.algorithmic_window_ms == 560
        for _ in range(4):
            backend.feed_chunk(sess, tone[:4000])
        stream_res = backend.finish_stream(sess)
        assert stream_res['text'] == 'ligar a luz da sala'
        assert stream_res['inference_mode'] == 'buffered_offline'
        assert stream_res['streaming_note']
        assert stream_res['inference_ms'] != 560.0 or stream_res['inference_ms'] >= 0.0

        # 13. unload releases model while keeping structured response clean
        ures = backend.unload()
        assert ures['ok'] is True
        assert ures['model_loaded'] is False
        assert backend.load_status == 'unloaded'
    finally:
        backend.close()


def test_14_unexpected_worker_death_and_oom_classification(tmp_path, monkeypatch):
    # Unexpected non-OOM crash during infer
    model_dir, _ = _setup_worker_env(tmp_path, monkeypatch, {'FAKE_NEMO_CRASH_INFER': '1'})
    backend = nemotron_mod.NemotronUnifiedBackend(model_dir, threads=2)
    try:
        backend.load()
        tone = np.ones(8000, dtype=np.float32) * 0.2
        with pytest.raises(AranduStructuredError) as exc_info:
            backend.recognize(tone)
        assert exc_info.value.code == 'MODEL_LOAD_FAILED'
        assert backend.load_status == 'model_load_failed'
    finally:
        backend.close()

    # Worker exit 137 / OutOfMemory -> classified as oom / MemoryError
    oom_dir = tmp_path / 'nemotron_oom'
    oom_dir.mkdir()
    site = _make_fake_stt_site(tmp_path)
    wrapper_oom = _make_python_wrapper(tmp_path, 'nemo_oom_w', {'PYTHONPATH': str(site), 'FAKE_NEMO_SIGKILL_LOAD': '1'})
    monkeypatch.setenv('ARANDU_NEMOTRON_PYTHON', str(wrapper_oom))
    (oom_dir / 'nemotron-labs-unified-asr-0.6b.nemo').write_bytes(b'CKPT' * 128)
    backend_oom = nemotron_mod.NemotronUnifiedBackend(oom_dir, threads=2)
    try:
        with pytest.raises(MemoryError):
            backend_oom.load()
        assert backend_oom.load_status == 'oom'
    finally:
        backend_oom.close()


def test_15_load_and_infer_timeouts_kill_worker_tree(tmp_path, monkeypatch):
    # Load timeout
    model_dir, _ = _setup_worker_env(tmp_path, monkeypatch, {'FAKE_NEMO_SLOW_LOAD': '5.0'})
    backend_load = nemotron_mod.NemotronUnifiedBackend(model_dir, threads=2, load_timeout_s=0.35)
    with pytest.raises(TimeoutError) as exc_l:
        backend_load.load()
    assert getattr(exc_l.value, 'code', None) == 'STT_TIMEOUT'
    assert backend_load.load_status == 'timeout'
    assert backend_load._proc is None

    # Infer timeout
    infer_dir = tmp_path / 'nemotron_infer_to'
    infer_dir.mkdir()
    site = _make_fake_stt_site(tmp_path)
    wrapper_inf = _make_python_wrapper(tmp_path, 'nemo_inf_to', {'PYTHONPATH': str(site), 'FAKE_NEMO_SLOW_INFER': '5.0'})
    monkeypatch.setenv('ARANDU_NEMOTRON_PYTHON', str(wrapper_inf))
    (infer_dir / 'nemotron-labs-unified-asr-0.6b.nemo').write_bytes(b'CKPT' * 128)

    backend_inf = nemotron_mod.NemotronUnifiedBackend(infer_dir, threads=2, infer_timeout_s=0.35)
    try:
        backend_inf.load()
        proc_before = backend_inf._proc
        assert proc_before is not None
        tone = np.ones(8000, dtype=np.float32) * 0.25
        with pytest.raises(TimeoutError) as exc_i:
            backend_inf.recognize(tone)
        assert getattr(exc_i.value, 'code', None) == 'STT_TIMEOUT'
        assert backend_inf.load_status == 'timeout'
        assert backend_inf._proc is None
        assert proc_before.poll() is not None
    finally:
        backend_inf.close()


# ---------------------------------------------------------------------------
# 16, 17, 18. Cache invalidation, apply validation/rollback & /api/stt/models
# ---------------------------------------------------------------------------

def test_16_runtime_cache_invalidation_on_remove_install_and_apply(tmp_path, monkeypatch):
    calls = {'count': 0}
    orig_probe = model_catalog._run_python_probe

    def _counting_probe(py_bin, checks, *, timeout):
        calls['count'] += 1
        return {'available': True, 'runtime_status': 'runtime_ready', 'error_code': None, 'note': None, 'detail': None}

    site = _make_fake_stt_site(tmp_path)
    monkeypatch.setenv('ARANDU_NEMOTRON_PYTHON', str(_make_python_wrapper(tmp_path, 'nemo_cache', {'PYTHONPATH': str(site)})))
    monkeypatch.setenv('ARANDU_RUNTIME_PROBE_TTL_S', '60')
    monkeypatch.setattr(model_catalog, '_run_python_probe', _counting_probe)

    model_catalog.probe_runtime_status('nemotron_unified_0_6b')
    model_catalog.probe_runtime_status('nemotron_unified_0_6b')
    assert calls['count'] == 1

    # remove() invalidates cache
    model_catalog.remove(tmp_path / 'models', 'nemotron_unified_0_6b')
    model_catalog.probe_runtime_status('nemotron_unified_0_6b')
    assert calls['count'] == 2

    # Explicit invalidation invalidates cache
    model_catalog.invalidate_runtime_cache('nemotron_unified_0_6b')
    model_catalog.probe_runtime_status('nemotron_unified_0_6b')
    assert calls['count'] == 3
    monkeypatch.setattr(model_catalog, '_run_python_probe', orig_probe)


def test_17_stt_lab_apply_validates_before_activating_and_rolls_back(tmp_path, monkeypatch):
    site = _make_fake_stt_site(tmp_path)
    monkeypatch.setenv('ARANDU_NEMOTRON_PYTHON', str(_make_python_wrapper(tmp_path, 'nemo_apply', {'PYTHONPATH': str(site)})))

    manager = _DummyManager(tmp_path / 'data', stt_model='fastconformer')
    lab = STTLab(manager)

    async def _failing_worker(body, timeout=90):
        assert body['command'] == 'validate'
        raise WorkerExecutionError('Memória insuficiente (OOM)', 'oom')

    monkeypatch.setattr(lab, 'worker', _failing_worker)

    with pytest.raises(WorkerExecutionError, match='Memória insuficiente'):
        asyncio.run(lab.apply({'model': 'nemotron_unified_0_6b', 'confirm': True}))

    # Previous model preserved; Assist was never restarted or overwritten
    assert manager.config['stt']['stt_model'] == 'fastconformer'
    assert manager.configured == []
    assert manager.started == []

    # Reflected cleanly in models() diagnostic load_status
    nem_dir = model_catalog.location(lab.root, 'nemotron_unified_0_6b')
    nem_dir.mkdir(parents=True, exist_ok=True)
    ckpt = nem_dir / 'nemotron-labs-unified-asr-0.6b.nemo'
    ckpt.write_bytes(b'x' * 128)
    orig_sizes = model_catalog.CATALOG['nemotron_unified_0_6b']['file_sizes']
    orig_hashes = model_catalog.CATALOG['nemotron_unified_0_6b']['sha256']
    import hashlib
    digest = hashlib.sha256(b'x' * 128).hexdigest()
    (nem_dir / 'manifest.json').write_text(
        json.dumps({'id': 'nemotron_unified_0_6b', 'revision': model_catalog.CATALOG['nemotron_unified_0_6b']['revision'], 'sha256': {'nemotron-labs-unified-asr-0.6b.nemo': digest}}),
        encoding='utf-8',
    )
    monkeypatch.setitem(model_catalog.CATALOG['nemotron_unified_0_6b'], 'file_sizes', {'nemotron-labs-unified-asr-0.6b.nemo': 128})
    monkeypatch.setitem(model_catalog.CATALOG['nemotron_unified_0_6b'], 'sha256', {'nemotron-labs-unified-asr-0.6b.nemo': digest})
    try:
        inv = asyncio.run(lab.models())
        nem_row = next(r for r in inv['models'] if r['id'] == 'nemotron_unified_0_6b')
        assert nem_row['load_status'] == 'oom'
        assert nem_row['diagnostic_state'] == 'oom'
    finally:
        model_catalog.CATALOG['nemotron_unified_0_6b']['file_sizes'] = orig_sizes
        model_catalog.CATALOG['nemotron_unified_0_6b']['sha256'] = orig_hashes


def test_18_api_stt_models_endpoint_contract_and_no_numpy_error(tmp_path, monkeypatch):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    site = _make_fake_stt_site(tmp_path)
    py_env = {'PYTHONPATH': str(site)}
    wrapper = _make_python_wrapper(tmp_path, 'all_py', py_env)
    whisper_bin = _make_executable_stub(tmp_path / ('whisper-server.cmd' if os.name == 'nt' else 'whisper-server'))
    transcribe_lib = tmp_path / 'libtranscribe.so'
    transcribe_lib.write_text('stub', encoding='utf-8')

    monkeypatch.setenv('ARANDU_STT_PYTHON', str(wrapper))
    monkeypatch.setenv('ARANDU_PHONON_PYTHON', str(wrapper))
    monkeypatch.setenv('ARANDU_NEMOTRON_PYTHON', str(wrapper))
    monkeypatch.setenv('ARANDU_WHISPER_SERVER_BIN', str(whisper_bin))
    monkeypatch.setenv('TRANSCRIBE_LIBRARY', str(transcribe_lib))

    manager = _DummyManager(tmp_path / 'data')
    lab = STTLab(manager)

    async def _run():
        app = web.Application()

        async def stt_models(request):
            return web.json_response(await lab.models(request.query.get('variant')))

        app.router.add_get('/api/stt/models', stt_models)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get('/api/stt/models')
            assert resp.status == 200
            raw = await resp.text()
            assert "No module named 'numpy'" not in raw
            data = json.loads(raw)
            by_id = {m['id']: m for m in data['models']}
            for expected in ('fastconformer', 'whisper_cpp_small', 'phonon_2_5', 'nemotron_unified_0_6b'):
                assert expected in by_id
                row = by_id[expected]
                assert row['runtime_status'] == 'runtime_ready'
                assert row['runtime_available'] is True
                assert row['runtime_note'] is None
                assert row['weights_status'] == 'model_not_installed'
                assert row['load_status'] == 'unloaded'
                assert row['diagnostic_state'] == 'model_not_installed'
                assert 'MODEL_RUNTIME_UNAVAILABLE' not in json.dumps(row)

    asyncio.run(_run())


def test_19_download_progress_and_error_reinstall_state_in_stt_lab(tmp_path, monkeypatch):
    site = _make_fake_stt_site(tmp_path)
    wrapper = _make_python_wrapper(tmp_path, 'all_py_dl', {'PYTHONPATH': str(site)})
    whisper_bin = _make_executable_stub(tmp_path / ('whisper-server.cmd' if os.name == 'nt' else 'whisper-server'))
    transcribe_lib = tmp_path / 'libtranscribe.so'
    transcribe_lib.write_text('stub', encoding='utf-8')
    monkeypatch.setenv('ARANDU_STT_PYTHON', str(wrapper))
    monkeypatch.setenv('ARANDU_PHONON_PYTHON', str(wrapper))
    monkeypatch.setenv('ARANDU_NEMOTRON_PYTHON', str(wrapper))
    monkeypatch.setenv('ARANDU_WHISPER_SERVER_BIN', str(whisper_bin))
    monkeypatch.setenv('TRANSCRIBE_LIBRARY', str(transcribe_lib))

    manager = _DummyManager(tmp_path / 'data', stt_model='nemotron_unified_0_6b')
    # Simulate an active STT process starting up while nemotron is incomplete/downloading
    manager.processes['stt'] = type('P', (), {'returncode': None})()
    lab = STTLab(manager)
    lab.root.mkdir(parents=True, exist_ok=True)
    key = ('nemotron_unified_0_6b', 'adapted')
    lab.operations[key] = {'state': 'downloading', 'weights_status': 'downloading'}
    (lab.root / '.install-nemotron_unified_0_6b.json').write_text(
        json.dumps({
            'state': 'downloading',
            'bytes_downloaded': 500_000_000,
            'total_bytes': 2_507_735_040,
            'percent': 19.9,
            'speed_mbps': 24.5,
            'phase': 'Baixando pesos...',
        }),
        encoding='utf-8',
    )

    inv_dl = asyncio.run(lab.models())
    nem_dl = next(r for r in inv_dl['models'] if r['id'] == 'nemotron_unified_0_6b')
    assert nem_dl['state'] == 'downloading'
    assert nem_dl['incomplete'] is False
    assert 'error' not in nem_dl
    assert nem_dl['percent'] == 19.9
    assert nem_dl['speed_mbps'] == 24.5
    assert nem_dl['phase'] == 'Baixando pesos...'

    # On install failure, state must stay 'error' (not overwritten to 'loading') so reinstall stays enabled
    lab.operations[key] = {
        'state': 'error',
        'incomplete': True,
        'weights_status': 'model_not_installed',
        'error': 'Falha de rede. Cache anterior preservado; tente novamente.',
    }
    inv_err = asyncio.run(lab.models())
    nem_err = next(r for r in inv_err['models'] if r['id'] == 'nemotron_unified_0_6b')
    assert nem_err['state'] == 'error'
    assert nem_err['incomplete'] is True
    assert 'tente novamente' in nem_err['error']
