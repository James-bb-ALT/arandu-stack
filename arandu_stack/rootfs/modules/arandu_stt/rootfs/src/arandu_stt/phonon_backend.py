"""Persistent in-memory Phonon 2.5 backend using fermion-research (direct or isolated venv worker)."""
from __future__ import annotations

import base64
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import numpy as np

from stt_errors import AranduStructuredError

DEFAULT_PHONON_PYTHON = Path('/opt/venvs/phonon/bin/python')
DEFAULT_PHONON_TIMEOUT_SEC = float(os.environ.get('PHONON_TIMEOUT_SEC', '60'))


def _phonon_python_path() -> Path | None:
    env_override = os.environ.get('ARANDU_PHONON_PYTHON')
    if env_override:
        cand = Path(env_override)
        return cand if cand.is_file() else None
    return DEFAULT_PHONON_PYTHON if DEFAULT_PHONON_PYTHON.is_file() else None


def is_phonon_runtime_available() -> bool:
    return importlib.util.find_spec('fermion') is not None or _phonon_python_path() is not None


def _load_fermion_direct(model_dir: Path, threads: int):
    weights = model_dir / 'phonon-2.5.fsq'
    unpacked = model_dir / 'unpacked'
    os.environ['FERMION_CPU_THREADS'] = str(threads)
    os.environ.setdefault('FERMION_DEVICE', 'cpu')
    import fermion  # type: ignore

    target = model_dir
    # If directory only contains phonon-2.5.fsq, unpack offline once into unpacked/
    if not (model_dir / 'config.json').is_file() or not (model_dir / 'packed_manifest.json').is_file():
        if not (unpacked / 'config.json').is_file() or not (unpacked / 'packed_manifest.json').is_file():
            if not weights.is_file() or weights.stat().st_size < 10_000_000:
                raise FileNotFoundError('Pesos phonon-2.5.fsq ausentes ou incompletos')
            from fermion._speech import fetch  # type: ignore
            unpacked.mkdir(parents=True, exist_ok=True)
            fetch.unpack_any(weights, unpacked)
        target = unpacked
    if not hasattr(fermion, 'load_speech'):
        raise AranduStructuredError(
            'MODEL_RUNTIME_UNAVAILABLE',
            'API fermion.load_speech ausente na versão instalada de fermion-research',
            stage='load',
        )
    return fermion.load_speech(str(target), quiet=True)


def _extract_text(out) -> str:
    if isinstance(out, tuple) and out:
        return str(out[0] or '').strip()
    if isinstance(out, dict):
        return str(out.get('text', '') or '').strip()
    return str(getattr(out, 'text', None) or out or '').strip()


class PhononBackend:
    def __init__(self, model_dir, threads=2, timeout_sec: float | None = None):
        self.model_dir = Path(model_dir)
        self.threads = int(threads)
        self.timeout_sec = float(timeout_sec if timeout_sec is not None else DEFAULT_PHONON_TIMEOUT_SEC)
        self._model = None
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self.load_ms = 0.0

    def load(self):
        weights = self.model_dir / 'phonon-2.5.fsq'
        has_unpacked = (self.model_dir / 'config.json').is_file() and (self.model_dir / 'packed_manifest.json').is_file()
        if not has_unpacked and (not weights.is_file() or weights.stat().st_size < 10_000_000):
            raise FileNotFoundError('Pesos phonon-2.5.fsq ausentes ou incompletos')

        start = time.perf_counter()
        # 1) Direct in-process import when running inside /opt/venvs/phonon or mocked in tests
        if 'fermion' in sys.modules or importlib.util.find_spec('fermion') is not None:
            self._model = _load_fermion_direct(self.model_dir, self.threads)
            self.load_ms = (time.perf_counter() - start) * 1000.0
            return self

        # 2) Isolated persistent worker in /opt/venvs/phonon
        py_bin = _phonon_python_path()
        if py_bin is None:
            raise AranduStructuredError(
                'MODEL_RUNTIME_UNAVAILABLE',
                'Runtime fermion-research (Phonon 2.5) não disponível em /opt/venvs/phonon',
                stage='load',
            )
        worker_env = {**os.environ, 'FERMION_CPU_THREADS': str(self.threads), 'FERMION_DEVICE': 'cpu'}
        self._proc = subprocess.Popen(
            [str(py_bin), str(Path(__file__).resolve()), '--worker'],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            env=worker_env,
        )
        try:
            resp = self._rpc({'cmd': 'load', 'model_dir': str(self.model_dir), 'threads': self.threads}, timeout=self.timeout_sec)
            if not resp.get('ok'):
                raise AranduStructuredError(
                    resp.get('error_code', 'MODEL_LOAD_FAILED'),
                    resp.get('error', 'Falha ao carregar Phonon 2.5 no worker isolado'),
                    stage='load',
                )
            self.load_ms = float(resp.get('load_ms') or (time.perf_counter() - start) * 1000.0)
        except BaseException:
            self.close()
            raise
        return self

    def _rpc(self, payload: dict, *, timeout: float) -> dict:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.stdout is None:
            raise AranduStructuredError('MODEL_RUNTIME_UNAVAILABLE', 'Worker Phonon 2.5 encerrado', stage='infer')
        result_box: dict = {}
        err_box: list[BaseException] = []

        def _io():
            try:
                proc.stdin.write(json.dumps(payload) + '\n')
                proc.stdin.flush()
                line = proc.stdout.readline()
                if not line:
                    stderr_tail = proc.stderr.read(500) if proc.stderr else ''
                    raise RuntimeError(f'Worker Phonon encerrou inesperadamente: {stderr_tail.strip()}')
                result_box['data'] = json.loads(line)
            except BaseException as exc:
                err_box.append(exc)

        th = threading.Thread(target=_io, daemon=True)
        th.start()
        th.join(timeout=timeout)
        if th.is_alive():
            self._terminate_proc()
            raise TimeoutError(f'STT_TIMEOUT: Phonon 2.5 excedeu {timeout:.1f}s')
        if err_box:
            raise err_box[0]
        return result_box['data']

    def recognize(self, audio):
        if self._model is None and self._proc is None:
            raise RuntimeError('Phonon 2.5 não carregado')
        arr = np.asarray(audio, dtype=np.float32).reshape(-1)
        if arr.size == 0:
            return '', 0.0
        with self._lock:
            start = time.perf_counter()
            if self._model is not None:
                if hasattr(self._model, 'transcribe_array'):
                    out = self._model.transcribe_array(arr)
                elif hasattr(self._model, 'transcribe'):
                    out = self._model.transcribe(arr)
                else:
                    raise AranduStructuredError(
                        'MODEL_RUNTIME_UNAVAILABLE',
                        'Método transcribe_array ausente no runtime Phonon 2.5',
                        stage='infer',
                    )
                return _extract_text(out), (time.perf_counter() - start) * 1000.0

            b64 = base64.b64encode(arr.astype('<f4').tobytes()).decode('ascii')
            resp = self._rpc({'cmd': 'recognize', 'audio_f32_b64': b64}, timeout=self.timeout_sec)
            if not resp.get('ok'):
                raise AranduStructuredError(
                    resp.get('error_code', 'MODEL_LOAD_FAILED'),
                    resp.get('error', 'Erro na inferência Phonon 2.5'),
                    stage='infer',
                )
            return str(resp.get('text', '')).strip(), float(resp.get('inference_ms') or (time.perf_counter() - start) * 1000.0)

    def _terminate_proc(self):
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
        except Exception:
            pass
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            if pipe is not None:
                try:
                    pipe.close()
                except Exception:
                    pass

    def close(self):
        with self._lock:
            if self._model is not None and hasattr(self._model, 'close'):
                try:
                    self._model.close()
                except Exception:
                    pass
            self._model = None
            if self._proc is not None:
                if self._proc.poll() is None and self._proc.stdin is not None:
                    try:
                        self._proc.stdin.write(json.dumps({'cmd': 'close'}) + '\n')
                        self._proc.stdin.flush()
                    except Exception:
                        pass
                self._terminate_proc()


def _worker_main() -> int:
    model = None
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            cmd = req.get('cmd')
            if cmd == 'load':
                t0 = time.perf_counter()
                model = _load_fermion_direct(Path(req['model_dir']), int(req.get('threads', 2)))
                print(json.dumps({'ok': True, 'load_ms': (time.perf_counter() - t0) * 1000.0}), flush=True)
            elif cmd == 'recognize':
                if model is None:
                    print(json.dumps({'ok': False, 'error_code': 'MODEL_LOAD_FAILED', 'error': 'Modelo não carregado'}), flush=True)
                    continue
                t0 = time.perf_counter()
                raw_bytes = base64.b64decode(req['audio_f32_b64'])
                arr = np.frombuffer(raw_bytes, dtype='<f4')
                out = model.transcribe_array(arr)
                print(json.dumps({
                    'ok': True,
                    'text': _extract_text(out),
                    'inference_ms': (time.perf_counter() - t0) * 1000.0,
                }), flush=True)
            elif cmd == 'close':
                print(json.dumps({'ok': True}), flush=True)
                return 0
            else:
                print(json.dumps({'ok': False, 'error': f'Comando desconhecido: {cmd}'}), flush=True)
        except Exception as exc:
            code = getattr(exc, 'code', 'MODEL_LOAD_FAILED')
            print(json.dumps({'ok': False, 'error_code': code, 'error': str(exc)}), flush=True)
    return 0


if __name__ == '__main__' and '--worker' in sys.argv:
    raise SystemExit(_worker_main())
