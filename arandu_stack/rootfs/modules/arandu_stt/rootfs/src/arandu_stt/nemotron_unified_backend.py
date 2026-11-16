"""Isolated NVIDIA Nemotron Labs Unified ASR 0.6B backend with persistent NeMo worker."""
from __future__ import annotations

import atexit
import base64
from collections import deque
from dataclasses import dataclass, field
import gc
import inspect
import io
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any
import wave

from stt_errors import AranduRuntimeUnavailableError, AranduStructuredError

DEFAULT_NEMOTRON_PYTHON = Path('/opt/venvs/nemotron/bin/python')
CHECKPOINT_FILENAME = 'nemotron-labs-unified-asr-0.6b.nemo'
LFS_POINTER_PREFIX = b'version https://git-lfs.github.com/spec/v1'
DEFAULT_MEMORY_LIMIT_MB = 4608
DEFAULT_LOAD_TIMEOUT_S = float(os.environ.get('ARANDU_NEMOTRON_LOAD_TIMEOUT_S', '180'))
DEFAULT_INFER_TIMEOUT_S = float(os.environ.get('ARANDU_NEMOTRON_INFER_TIMEOUT_S', '90'))
DEFAULT_HEALTH_TIMEOUT_S = float(os.environ.get('ARANDU_NEMOTRON_HEALTH_TIMEOUT_S', '10'))
FIXED_LANGUAGE = 'pt'

RESIDENCY_POLICIES = frozenset({'RESIDENT', 'LAZY', 'OFF'})

STREAMING_PROFILES: dict[str, dict[str, int]] = {
    'precision': {'chunk_ms': 560, 'right_context_ms': 560, 'left_context_ms': 5600},
    'balanced': {'chunk_ms': 160, 'right_context_ms': 400, 'left_context_ms': 5600},
    'fast': {'chunk_ms': 80, 'right_context_ms': 160, 'left_context_ms': 5600},
    'experimental': {'chunk_ms': 80, 'right_context_ms': 80, 'left_context_ms': 5600},
}

ALL_PROFILES: dict[str, dict[str, int]] = {
    'offline': {'chunk_ms': 0, 'right_context_ms': 0, 'left_context_ms': 0},
    **STREAMING_PROFILES,
}


class AranduNemotronTimeoutError(AranduStructuredError, TimeoutError):
    """Structured timeout error that also satisfies TimeoutError handlers."""

    def __init__(self, message: str, *, stage: str = 'infer') -> None:
        super().__init__('STT_TIMEOUT', message, stage=stage)


def _nemotron_python_path() -> Path | None:
    if 'ARANDU_NEMOTRON_PYTHON' in os.environ:
        raw = os.environ['ARANDU_NEMOTRON_PYTHON'].strip()
        if not raw:
            return None
        cand = Path(raw)
        return cand if cand.is_file() else None
    return DEFAULT_NEMOTRON_PYTHON if DEFAULT_NEMOTRON_PYTHON.is_file() else None


def is_nemotron_runtime_available() -> bool:
    """Return True only when the isolated Nemotron interpreter exists (never rely on current env alone)."""
    return _nemotron_python_path() is not None


def _min_checkpoint_bytes() -> int:
    raw = os.environ.get('ARANDU_NEMOTRON_MIN_BYTES')
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return 100_000_000


def validate_fixed_portuguese(
    language: str | None = FIXED_LANGUAGE,
    translate: bool = False,
    detect_language: bool = False,
) -> str:
    """Enforce fixed Portuguese language without translation or auto-detection."""
    if bool(translate):
        raise ValueError('Tradução automática não é permitida para Nemotron; translate deve permanecer false.')
    if bool(detect_language):
        raise ValueError('Detecção automática de idioma não é permitida para Nemotron.')
    if not isinstance(language, str) or language.strip().lower() not in ('pt', 'pt-br', 'pt_br', 'pt-pt'):
        raise ValueError("Idioma fixo em português ('pt'); alteração para outros idiomas ou 'auto' não é permitida.")
    return 'pt'


def _audio_to_pcm16_and_wav_bytes(audio: Any) -> tuple[bytes, bytes, int, bool]:
    """Normalize input audio to 16 kHz mono PCM16 bytes and WAV container bytes."""
    import numpy as np

    if audio is None:
        return b'', b'', 0, True
    if isinstance(audio, (bytes, bytearray, memoryview)):
        raw = bytes(audio)
        if not raw:
            return b'', b'', 0, True
        if len(raw) % 2 != 0:
            raise ValueError('PCM16 inválido: número ímpar de bytes')
        pcm_arr = np.frombuffer(raw, dtype='<i2')
        sample_count = int(pcm_arr.size)
        pcm_bytes = raw
        is_silent = sample_count == 0 or not bool(np.any(pcm_arr))
    else:
        arr = np.asarray(audio)
        if arr.size == 0:
            return b'', b'', 0, True
        if arr.ndim > 1:
            arr = arr.reshape(-1)
        if np.issubdtype(arr.dtype, np.integer):
            pcm16 = np.clip(arr, -32768, 32767).astype('<i2')
        else:
            pcm16 = np.clip(np.round(arr.astype(np.float32) * 32767.0), -32768, 32767).astype('<i2')
        pcm_bytes = pcm16.tobytes()
        sample_count = int(pcm16.size)
        is_silent = sample_count == 0 or not bool(np.any(pcm16))

    if is_silent:
        return pcm_bytes, b'', sample_count, True

    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(pcm_bytes)
    return pcm_bytes, buf.getvalue(), sample_count, False


def _tail_implies_oom(lines: deque[str], returncode: int | None) -> bool:
    if returncode in (-9, 137):
        return True
    if os.name == 'posix' and hasattr(signal, 'SIGKILL') and returncode == -signal.SIGKILL:
        return True
    joined = ' '.join(lines).lower()
    return any(
        tok in joined
        for tok in (
            'out of memory',
            'cannot allocate memory',
            'std::bad_alloc',
            'memoryerror',
            'killed',
            'oom',
        )
    )


def _process_tree_rss_mb(pid: int | None) -> float:
    if not pid:
        return 0.0
    try:
        import psutil

        proc = psutil.Process(pid)
        total = proc.memory_info().rss
        children_fn = getattr(proc, 'children', None)
        if callable(children_fn):
            for child in children_fn(recursive=True) or ():
                try:
                    total += child.memory_info().rss
                except (psutil.Error, AttributeError):
                    pass
        return round(total / 1_048_576, 2)
    except Exception:
        return 0.0


def _kill_process_tree(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    try:
        import psutil

        try:
            parent = psutil.Process(proc.pid)
            descendants = parent.children(recursive=True)
            for child in descendants:
                try:
                    child.terminate()
                except psutil.Error:
                    pass
            _, alive = psutil.wait_procs(descendants, timeout=1.5)
            for child in alive:
                try:
                    child.kill()
                except psutil.Error:
                    pass
        except psutil.Error:
            pass
    except Exception:
        pass

    try:
        if proc.poll() is None:
            if os.name == 'posix' and hasattr(os, 'killpg'):
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except OSError:
                    proc.terminate()
            else:
                proc.terminate()
            try:
                proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                if os.name == 'posix' and hasattr(os, 'killpg'):
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except OSError:
                        proc.kill()
                else:
                    proc.kill()
                proc.wait(timeout=2.0)
    except Exception:
        pass

    for pipe in (proc.stdin, proc.stdout, proc.stderr):
        if pipe is not None:
            try:
                pipe.close()
            except Exception:
                pass


@dataclass
class NemotronStreamSession:
    profile: str
    target_lang: str = 'pt'
    chunks: list[Any] = field(default_factory=list)
    total_samples: int = 0
    inference_ms: float = 0.0
    inference_mode: str = 'buffered_offline'
    streaming_note: str | None = None

    @property
    def algorithmic_window_ms(self) -> int:
        spec = ALL_PROFILES[self.profile]
        return spec['chunk_ms'] + spec['right_context_ms']

    @property
    def algorithmic_latency_ms(self) -> int:
        return self.algorithmic_window_ms


class NemotronUnifiedBackend:
    """Isolated NeMo Speech worker controller for Nemotron Unified ASR 0.6B."""

    def __init__(
        self,
        model_dir,
        threads: int = 2,
        target_lang: str = 'pt',
        streaming_profile: str = 'offline',
        residency_policy: str = 'LAZY',
        memory_limit_mb: int = DEFAULT_MEMORY_LIMIT_MB,
        load_timeout_s: float | None = None,
        infer_timeout_s: float | None = None,
    ):
        self.model_dir = Path(model_dir)
        self.threads = max(1, min(4, int(threads)))
        self.target_lang = validate_fixed_portuguese(target_lang)
        if streaming_profile not in ALL_PROFILES:
            raise ValueError(f'Perfil Nemotron inválido: {streaming_profile}')
        policy = str(residency_policy or 'LAZY').upper()
        if policy not in RESIDENCY_POLICIES:
            raise ValueError(f'Política de residência inválida: {residency_policy}')
        self.streaming_profile = streaming_profile
        self.residency_policy = policy
        self.memory_limit_mb = int(memory_limit_mb)
        self.load_timeout_s = float(load_timeout_s if load_timeout_s is not None else DEFAULT_LOAD_TIMEOUT_S)
        self.infer_timeout_s = float(infer_timeout_s if infer_timeout_s is not None else DEFAULT_INFER_TIMEOUT_S)

        self._proc: subprocess.Popen | None = None
        self._stderr_lines: deque[str] = deque(maxlen=60)
        self._stderr_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._cancelled = threading.Event()
        self._loaded = False
        self._atexit_registered = False

        self.load_ms = 0.0
        self.cold_load_ms = 0.0
        self.last_inference_ms = 0.0
        self.last_total_ms = 0.0
        self.last_rss_mb = 0.0
        self.last_inference_mode = 'offline'
        self.last_streaming_note: str | None = None
        self.streaming_capability = 'buffered_offline'
        self.resolved_lang = 'pt'
        self.load_status = 'unloaded'
        self.last_error: str | None = None
        self.last_metrics: dict[str, Any] = {}

    def _checkpoint_path(self) -> Path:
        path = self.model_dir / CHECKPOINT_FILENAME
        if not path.is_file():
            raise FileNotFoundError(f'Checkpoint {CHECKPOINT_FILENAME} ausente em {self.model_dir}')
        size = path.stat().st_size
        if size == 0:
            raise FileNotFoundError(f'Checkpoint {CHECKPOINT_FILENAME} vazio em {self.model_dir}')
        with path.open('rb') as fh:
            head = fh.read(len(LFS_POINTER_PREFIX))
        if head.startswith(LFS_POINTER_PREFIX):
            raise ValueError('Checkpoint Nemotron é um ponteiro Git LFS inválido; baixe novamente.')
        if size < _min_checkpoint_bytes():
            raise ValueError(f'Checkpoint Nemotron corrompido ou truncado ({size} bytes)')
        return path

    def _drain_stderr(self, stream) -> None:
        try:
            for raw in stream:
                txt = raw.decode('utf-8', errors='replace').strip()
                if txt:
                    self._stderr_lines.append(txt)
        except Exception:
            pass

    def _spawn_worker(self) -> subprocess.Popen:
        py_bin = _nemotron_python_path()
        if py_bin is None:
            raise AranduRuntimeUnavailableError(
                'NVIDIA NeMo isolado (/opt/venvs/nemotron) indisponível neste ambiente',
                stage='load',
            )
        worker_script = str(Path(__file__).resolve())
        popen_kwargs: dict[str, Any] = {
            'stdin': subprocess.PIPE,
            'stdout': subprocess.PIPE,
            'stderr': subprocess.PIPE,
            'text': False,
            'bufsize': 0,
            'close_fds': True,
        }
        if os.name == 'posix':
            popen_kwargs['start_new_session'] = True

        self._stderr_lines.clear()
        env = dict(os.environ)
        env['TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD'] = '1'
        env['OMP_NUM_THREADS'] = str(self.threads)
        env['OPENBLAS_NUM_THREADS'] = str(self.threads)
        env['MKL_NUM_THREADS'] = str(self.threads)
        env['PYTHONUNBUFFERED'] = '1'
        popen_kwargs['env'] = env

        proc = subprocess.Popen([str(py_bin), worker_script, '--worker'], **popen_kwargs)
        self._proc = proc
        if proc.stderr is not None:
            self._stderr_thread = threading.Thread(
                target=self._drain_stderr, args=(proc.stderr,), daemon=True, name='nemotron-stderr'
            )
            self._stderr_thread.start()
        if not self._atexit_registered:
            atexit.register(self.close)
            self._atexit_registered = True
        return proc

    def _rpc(self, payload: dict[str, Any], *, timeout: float, stage: str) -> dict[str, Any]:
        proc = self._proc
        if proc is None or proc.poll() is not None or proc.stdin is None or proc.stdout is None:
            rc = None if proc is None else proc.poll()
            if _tail_implies_oom(self._stderr_lines, rc):
                self.load_status = 'oom'
                raise MemoryError(f'Worker Nemotron encerrado por memória insuficiente (exit={rc})')
            self.load_status = 'model_load_failed'
            tail = ' | '.join(list(self._stderr_lines)[-6:])
            raise AranduStructuredError(
                'MODEL_LOAD_FAILED',
                f'Worker Nemotron indisponível (exit={rc})' + (f': {tail}' if tail else ''),
                stage=stage,
            )

        encoded = (json.dumps(payload, ensure_ascii=False) + '\n').encode('utf-8')
        result_box: dict[str, Any] = {}
        err_box: list[BaseException] = []

        def _io() -> None:
            try:
                proc.stdin.write(encoded)
                proc.stdin.flush()
                while True:
                    line_bytes = proc.stdout.readline()
                    if not line_bytes:
                        try:
                            rc_inner = proc.wait(timeout=1.5)
                        except subprocess.TimeoutExpired:
                            rc_inner = proc.poll()
                        if self._stderr_thread is not None and self._stderr_thread.is_alive():
                            self._stderr_thread.join(timeout=0.5)
                        if _tail_implies_oom(self._stderr_lines, rc_inner):
                            raise MemoryError(f'Worker Nemotron encerrado por OOM/SIGKILL (exit={rc_inner})')
                        tail_inner = ' | '.join(list(self._stderr_lines)[-6:])
                        raise AranduStructuredError(
                            'MODEL_LOAD_FAILED',
                            f'Worker Nemotron encerrou inesperadamente (exit={rc_inner})' + (f': {tail_inner}' if tail_inner else ''),
                            stage=stage,
                        )
                    line = line_bytes.decode('utf-8', errors='replace').strip()
                    if not line:
                        continue
                    if line.startswith('{') and line.endswith('}'):
                        try:
                            result_box['data'] = json.loads(line)
                            return
                        except ValueError:
                            continue
            except BaseException as exc:
                err_box.append(exc)

        th = threading.Thread(target=_io, daemon=True, name='nemotron-rpc')
        th.start()
        deadline = time.monotonic() + float(timeout)
        while th.is_alive():
            if self._cancelled.is_set():
                self.load_status = 'unloaded'
                self._terminate_proc()
                raise InterruptedError('Operação Nemotron cancelada')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.load_status = 'timeout'
                self._terminate_proc()
                raise AranduNemotronTimeoutError(
                    f'Timeout após {timeout:.1f}s em {stage} do Nemotron Unified ASR',
                    stage=stage,
                )
            th.join(timeout=min(0.1, remaining))

        if err_box:
            exc = err_box[0]
            if isinstance(exc, MemoryError):
                self.load_status = 'oom'
            elif isinstance(exc, TimeoutError):
                self.load_status = 'timeout'
            else:
                self.load_status = 'model_load_failed'
            self.last_error = str(exc)
            self._terminate_proc()
            raise exc
        return result_box['data']

    def process_rss_mb(self) -> float:
        """Return worker process-tree RSS in MiB (or last measured value if unloaded)."""
        if self._proc is not None and self._proc.poll() is None:
            rss = _process_tree_rss_mb(self._proc.pid)
            if rss > 0:
                self.last_rss_mb = rss
                return rss
        return self.last_rss_mb

    def _enforce_memory_limit(self, reported_rss: float | None = None) -> float:
        rss = max(float(reported_rss or 0.0), self.process_rss_mb())
        self.last_rss_mb = rss
        if self.memory_limit_mb > 0 and rss > self.memory_limit_mb:
            self.load_status = 'oom'
            self.last_error = f'RSS Nemotron ({rss:.1f} MiB) excedeu limite ({self.memory_limit_mb} MiB)'
            self._terminate_proc()
            raise MemoryError(self.last_error)
        return rss

    def health(self) -> dict[str, Any]:
        """Query persistent worker health, loaded state, and RSS."""
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                return {
                    'ok': False,
                    'status': 'stopped',
                    'model_loaded': False,
                    'load_status': self.load_status,
                    'rss_mb': 0.0,
                }
            resp = self._rpc({'cmd': 'health'}, timeout=DEFAULT_HEALTH_TIMEOUT_S, stage='health')
            if resp.get('rss_mb'):
                self.last_rss_mb = float(resp['rss_mb'])
            return resp

    def load(self) -> 'NemotronUnifiedBackend':
        if self.residency_policy == 'OFF':
            raise AranduStructuredError(
                'MODEL_LOAD_FAILED',
                'Política de residência OFF impede carregamento ativo do Nemotron',
                stage='load',
            )
        if not is_nemotron_runtime_available():
            self.load_status = 'runtime_missing'
            raise AranduRuntimeUnavailableError(
                'NVIDIA NeMo isolado (/opt/venvs/nemotron) indisponível neste ambiente',
                stage='load',
            )
        checkpoint_path = self._checkpoint_path()

        with self._lock:
            self._cancelled.clear()
            if self._loaded and self._proc is not None and self._proc.poll() is None:
                return self

            self.load_status = 'loading'
            self.last_error = None
            t0 = time.perf_counter()
            if self._proc is None or self._proc.poll() is not None:
                self._spawn_worker()

            health_resp = self._rpc({'cmd': 'health'}, timeout=DEFAULT_HEALTH_TIMEOUT_S, stage='health')
            if not health_resp.get('ok'):
                raise AranduStructuredError('MODEL_LOAD_FAILED', 'Worker Nemotron não respondeu ao health check', stage='load')

            resp = self._rpc(
                {
                    'cmd': 'load',
                    'checkpoint_path': str(checkpoint_path),
                    'threads': self.threads,
                    'target_lang': self.target_lang,
                },
                timeout=self.load_timeout_s,
                stage='load',
            )
            if not resp.get('ok'):
                err_code = resp.get('error_code', 'MODEL_LOAD_FAILED')
                err_msg = resp.get('error', 'Falha ao carregar Nemotron Unified ASR')
                if err_code == 'oom':
                    self.load_status = 'oom'
                    self._terminate_proc()
                    raise MemoryError(err_msg)
                self.load_status = 'model_load_failed'
                self.last_error = err_msg
                if err_code == 'MODEL_RUNTIME_UNAVAILABLE':
                    raise AranduRuntimeUnavailableError(err_msg, stage='load')
                raise AranduStructuredError(err_code, err_msg, stage='load')

            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            self.load_ms = float(resp.get('load_ms') or elapsed_ms)
            self.cold_load_ms = elapsed_ms
            self.resolved_lang = str(resp.get('resolved_lang') or self.target_lang)
            self.streaming_capability = str(resp.get('streaming_capability') or 'buffered_offline')
            self._enforce_memory_limit(resp.get('rss_mb'))
            self._loaded = True
            self.load_status = 'model_ready'
            return self

    def recognize(
        self,
        audio: Any,
        target_lang: str | None = None,
        *,
        mode: str | None = None,
        streaming_profile: str | None = None,
    ) -> tuple[str, float]:
        lang = validate_fixed_portuguese(target_lang or self.target_lang)
        prof = streaming_profile or self.streaming_profile
        if prof not in ALL_PROFILES:
            raise ValueError(f'Perfil Nemotron inválido: {prof}')
        req_mode = mode or ('offline' if prof == 'offline' else 'streaming')
        if req_mode not in ('offline', 'streaming'):
            raise ValueError(f'Modo Nemotron inválido: {req_mode}')

        if not self._loaded or self._proc is None or self._proc.poll() is not None:
            if self.residency_policy == 'LAZY':
                self.load()
            else:
                raise RuntimeError('Nemotron Unified ASR não carregado')

        pcm_bytes, wav_bytes, sample_count, is_silent = _audio_to_pcm16_and_wav_bytes(audio)
        if sample_count == 0 or is_silent:
            self.last_inference_ms = 0.0
            self.last_total_ms = 0.0
            self.last_inference_mode = 'offline' if req_mode == 'offline' else self.streaming_capability
            self.last_metrics = {
                'inference_ms': 0.0,
                'cold_load_ms': self.cold_load_ms,
                'inference_mode': self.last_inference_mode,
                'silent': True,
                'rss_mb': self.process_rss_mb(),
            }
            return '', 0.0

        tmp = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
        tmp_path = Path(tmp.name)
        tmp.write(wav_bytes)
        tmp.close()

        spec = ALL_PROFILES[prof]
        try:
            with self._lock:
                self._cancelled.clear()
                started = time.perf_counter()
                resp = self._rpc(
                    {
                        'cmd': 'transcribe',
                        'wav_path': str(tmp_path),
                        'language': lang,
                        'mode': req_mode,
                        'profile': prof,
                        'chunk_ms': spec['chunk_ms'],
                        'right_context_ms': spec['right_context_ms'],
                        'left_context_ms': spec['left_context_ms'],
                    },
                    timeout=self.infer_timeout_s,
                    stage='infer',
                )
                if not resp.get('ok'):
                    err_code = resp.get('error_code', 'MODEL_LOAD_FAILED')
                    err_msg = resp.get('error', 'Falha na inferência Nemotron Unified ASR')
                    if err_code == 'oom':
                        self.load_status = 'oom'
                        self._terminate_proc()
                        raise MemoryError(err_msg)
                    raise AranduStructuredError(err_code, err_msg, stage='infer')

                wall_ms = (time.perf_counter() - started) * 1000.0
                inf_ms = float(resp.get('inference_ms') or wall_ms)
                rss = self._enforce_memory_limit(resp.get('rss_mb'))
                audio_s = max(sample_count / 16000.0, 1e-6)
                self.last_inference_ms = inf_ms
                self.last_total_ms = self.cold_load_ms + inf_ms
                self.last_inference_mode = str(resp.get('inference_mode') or req_mode)
                self.last_streaming_note = resp.get('streaming_note')
                self.last_metrics = {
                    'inference_ms': round(inf_ms, 3),
                    'cold_load_ms': round(self.cold_load_ms, 3),
                    'total_ms': round(self.last_total_ms, 3),
                    'realtime_factor': round((inf_ms / 1000.0) / audio_s, 3),
                    'rss_mb': rss,
                    'inference_mode': self.last_inference_mode,
                    'streaming_note': self.last_streaming_note,
                    'chunks_processed': resp.get('chunks_processed'),
                    'first_partial_ms': resp.get('first_partial_ms'),
                }
                text = str(resp.get('text') or '').strip()
                return text, inf_ms
        finally:
            tmp_path.unlink(missing_ok=True)

    def start_stream(self, profile: str | None = None, target_lang: str | None = None) -> NemotronStreamSession:
        return self.create_stream(profile=profile, target_lang=target_lang)

    def create_stream(self, profile: str | None = None, target_lang: str | None = None) -> NemotronStreamSession:
        prof = profile or (self.streaming_profile if self.streaming_profile != 'offline' else 'balanced')
        if prof not in ALL_PROFILES:
            raise ValueError(f'Perfil streaming Nemotron inválido: {prof}')
        lang = validate_fixed_portuguese(target_lang or self.target_lang)
        return NemotronStreamSession(profile=prof, target_lang=lang)

    def feed_chunk(self, session: NemotronStreamSession, chunk: Any) -> dict[str, Any]:
        import numpy as np

        arr = np.asarray(chunk, dtype=np.float32).reshape(-1)
        if len(arr):
            session.chunks.append(arr)
            session.total_samples += len(arr)
        spec = ALL_PROFILES[session.profile]
        chunk_samples = max(1, int(16 * max(spec['chunk_ms'], 80)))
        return {
            'buffered_samples': session.total_samples,
            'ready_for_step': session.total_samples >= chunk_samples,
            'algorithmic_window_ms': session.algorithmic_window_ms,
            'algorithmic_latency_ms': session.algorithmic_latency_ms,
        }

    def finish_stream(self, session: NemotronStreamSession) -> dict[str, Any]:
        import numpy as np

        if not session.chunks:
            return {
                'text': '',
                'inference_ms': 0.0,
                'inference_mode': 'offline',
                'profile': session.profile,
                'algorithmic_window_ms': session.algorithmic_window_ms,
            }
        full_audio = np.concatenate(session.chunks)
        text, inf_ms = self.recognize(
            full_audio,
            target_lang=session.target_lang,
            mode='offline' if session.profile == 'offline' else 'streaming',
            streaming_profile=session.profile,
        )
        session.inference_ms = inf_ms
        session.inference_mode = self.last_inference_mode
        session.streaming_note = self.last_streaming_note
        return {
            'text': text,
            'inference_ms': round(inf_ms, 3),
            'inference_mode': self.last_inference_mode,
            'streaming_note': self.last_streaming_note,
            'profile': session.profile,
            'algorithmic_window_ms': session.algorithmic_window_ms,
            'rss_mb': self.process_rss_mb(),
        }

    def cancel(self) -> None:
        self._cancelled.set()
        self._loaded = False
        self._terminate_proc()

    def _terminate_proc(self) -> None:
        proc = self._proc
        self._proc = None
        self._loaded = False
        _kill_process_tree(proc)

    def unload(self) -> dict[str, Any]:
        """Unload model cleanly inside the worker and report status; kill if worker is unhealthy."""
        with self._lock:
            self._loaded = False
            if self._proc is None or self._proc.poll() is not None:
                self.load_status = 'unloaded'
                self._terminate_proc()
                return {'ok': True, 'model_loaded': False}
            try:
                resp = self._rpc({'cmd': 'unload'}, timeout=5.0, stage='unload')
                self.load_status = 'unloaded'
                self.last_rss_mb = float(resp.get('rss_mb') or self.process_rss_mb())
                return resp
            except Exception:
                self.load_status = 'unloaded'
                self._terminate_proc()
                return {'ok': True, 'model_loaded': False}

    def close(self) -> None:
        with self._lock:
            self._loaded = False
            if self._proc is not None and self._proc.poll() is None and self._proc.stdin is not None:
                try:
                    self._proc.stdin.write(b'{"cmd": "shutdown"}\n')
                    self._proc.stdin.flush()
                    self._proc.wait(timeout=2.0)
                except Exception:
                    pass
            self.load_status = 'unloaded'
            self._terminate_proc()

    def __del__(self) -> None:
        try:
            self._terminate_proc()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Isolated worker entrypoint (`python nemotron_unified_backend.py --worker`)
# ---------------------------------------------------------------------------

def _extract_text(out: Any) -> str:
    if out is None:
        return ''
    if isinstance(out, str):
        return out.strip()
    if isinstance(out, dict):
        for key in ('text', 'transcript', 'pred_text'):
            if out.get(key) is not None:
                return str(out[key]).strip()
        return ''
    if hasattr(out, 'text'):
        return str(getattr(out, 'text') or '').strip()
    if isinstance(out, (list, tuple)):
        if not out:
            return ''
        return _extract_text(out[0])
    return str(out).strip()


def _resolve_model_target_lang(model: Any, requested: str = 'pt') -> str:
    prompt_dict: dict[str, Any] = {}
    try:
        prompt_dict = dict(model.cfg.model_defaults.get('prompt_dictionary') or {})
    except Exception:
        prompt_dict = {}
    if not prompt_dict:
        return requested
    for cand in (requested, requested.lower(), 'pt', 'pt-BR', 'pt_BR', 'pt-br', 'pt-PT'):
        if cand in prompt_dict:
            return cand
    for key in prompt_dict:
        if str(key).lower().startswith('pt'):
            return str(key)
    if 'auto' in prompt_dict:
        return 'auto'
    return requested


def _detect_streaming_capability(model: Any) -> str:
    # Unified FastConformer-RNNT non-causal checkpoints do buffered windowing rather than cache-aware streaming.
    has_cache_step = callable(getattr(model, 'conformer_stream_step', None))
    is_noncausal_unified = False
    try:
        cfg_str = str(getattr(model, 'cfg', '')).lower()
        if 'unified' in cfg_str or 'noncausal' in cfg_str:
            is_noncausal_unified = True
    except Exception:
        pass
    if has_cache_step and not is_noncausal_unified:
        return 'streaming'
    return 'buffered_offline'


def _call_transcribe(model: Any, audio_paths: list[str], target_lang: str, *, partial_hypothesis: Any = None) -> Any:
    kwargs: dict[str, Any] = {'batch_size': 1, 'verbose': False}
    try:
        sig = inspect.signature(model.transcribe)
        params = sig.parameters
        accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
        if 'num_workers' in params or accepts_kwargs:
            kwargs['num_workers'] = 0
        if 'target_lang' in params or accepts_kwargs:
            kwargs['target_lang'] = target_lang
        if partial_hypothesis is not None and ('partial_hypothesis' in params or accepts_kwargs):
            kwargs['partial_hypothesis'] = partial_hypothesis
        if 'return_hypotheses' in params and partial_hypothesis is not None:
            kwargs['return_hypotheses'] = True
    except (TypeError, ValueError):
        pass
    if hasattr(model, 'set_inference_prompt'):
        try:
            model.set_inference_prompt(target_lang)
        except Exception:
            pass
    try:
        return model.transcribe(audio_paths, **kwargs)
    except TypeError:
        kwargs.pop('target_lang', None)
        kwargs.pop('num_workers', None)
        kwargs.pop('partial_hypothesis', None)
        kwargs.pop('return_hypotheses', None)
        return model.transcribe(audio_paths)


def _buffered_chunk_transcribe(
    model: Any,
    wav_path: str,
    target_lang: str,
    *,
    chunk_ms: int,
    right_context_ms: int,
    left_context_ms: int,
    capability: str,
) -> tuple[str, dict[str, Any]]:
    import numpy as np

    with wave.open(wav_path, 'rb') as wf:
        sr = wf.getframerate()
        nch = wf.getnchannels()
        sw = wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())
    if sw != 2 or nch != 1 or not raw:
        out = _call_transcribe(model, [wav_path], target_lang)
        return _extract_text(out), {
            'inference_mode': 'buffered_offline' if capability != 'streaming' else 'streaming',
            'chunks_processed': 1,
            'first_partial_ms': None,
            'streaming_note': 'Pipeline em buffer/offline no runtime NeMo; contexto esquerdo recomputado.',
        }

    samples = np.frombuffer(raw, dtype='<i2')
    total = int(samples.size)
    step = max(int(sr * max(chunk_ms, 80) / 1000.0), 1)
    right = max(int(sr * max(right_context_ms, 0) / 1000.0), 0)
    left = max(int(sr * max(left_context_ms, 5600) / 1000.0), step)

    if total <= step + right:
        out = _call_transcribe(model, [wav_path], target_lang)
        return _extract_text(out), {
            'inference_mode': 'buffered_offline',
            'chunks_processed': 1,
            'first_partial_ms': None,
            'streaming_note': 'Segmento menor que uma janela completa; transcrito em passo único sem cache de estado.',
        }

    # Step across chunk endpoints using bounded left+chunk+right context, measuring real wall-clock latency.
    chunks_processed = 0
    first_partial_ms: float | None = None
    last_text = ''
    cursor = step
    while True:
        end_idx = min(total, cursor + right)
        start_idx = max(0, cursor - left)
        window = samples[start_idx:end_idx]
        tmp = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
        tmp_chunk_path = Path(tmp.name)
        tmp.close()
        try:
            with wave.open(str(tmp_chunk_path), 'wb') as wf_out:
                wf_out.setnchannels(1)
                wf_out.setsampwidth(2)
                wf_out.setframerate(sr)
                wf_out.writeframes(window.tobytes())
            t_chunk = time.perf_counter()
            out = _call_transcribe(model, [str(tmp_chunk_path)], target_lang)
            last_text = _extract_text(out)
            chunks_processed += 1
            if first_partial_ms is None:
                first_partial_ms = round((time.perf_counter() - t_chunk) * 1000.0, 3)
        finally:
            tmp_chunk_path.unlink(missing_ok=True)
        if cursor >= total:
            break
        cursor = min(total, cursor + step)

    note = (
        f'Streaming em buffer sobre janela (chunk={chunk_ms}ms, contexto_dir={right_context_ms}ms, '
        f'contexto_esq={left_context_ms}ms) com recomputação do encoder (não cache-aware).'
    )
    return last_text, {
        'inference_mode': 'buffered_offline',
        'chunks_processed': chunks_processed,
        'first_partial_ms': first_partial_ms,
        'streaming_note': note,
    }


def _worker_main() -> int:
    os.environ.setdefault('TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD', '1')
    model = None
    resolved_lang = 'pt'
    capability = 'buffered_offline'

    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            cmd = req.get('cmd')
            if cmd == 'health':
                print(
                    json.dumps(
                        {
                            'ok': True,
                            'status': 'healthy',
                            'model_loaded': model is not None,
                            'pid': os.getpid(),
                            'rss_mb': _process_tree_rss_mb(os.getpid()),
                            'streaming_capability': capability,
                        }
                    ),
                    flush=True,
                )
            elif cmd == 'load':
                t0 = time.perf_counter()
                threads = max(1, min(4, int(req.get('threads', 2))))
                ckpt_path = Path(req['checkpoint_path'])
                target_lang = str(req.get('target_lang') or 'pt')
                if not ckpt_path.is_file():
                    print(
                        json.dumps({'ok': False, 'error_code': 'MODEL_LOAD_FAILED', 'error': f'Checkpoint ausente: {ckpt_path}'}),
                        flush=True,
                    )
                    continue
                import torch
                import nemo.collections.asr as nemo_asr  # type: ignore

                torch.set_num_threads(threads)
                torch.set_grad_enabled(False)
                asr_cls = getattr(nemo_asr.models, 'ASRModel', None)
                if asr_cls is None:
                    raise ImportError('nemo.collections.asr.models.ASRModel indisponível')
                model = asr_cls.restore_from(restore_path=str(ckpt_path), map_location='cpu')
                if hasattr(model, 'eval'):
                    model.eval()
                if hasattr(model, 'freeze'):
                    model.freeze()
                resolved_lang = _resolve_model_target_lang(model, target_lang)
                if hasattr(model, 'set_inference_prompt'):
                    try:
                        model.set_inference_prompt(resolved_lang)
                    except Exception:
                        pass
                capability = _detect_streaming_capability(model)
                load_ms = (time.perf_counter() - t0) * 1000.0
                print(
                    json.dumps(
                        {
                            'ok': True,
                            'load_ms': round(load_ms, 3),
                            'rss_mb': _process_tree_rss_mb(os.getpid()),
                            'resolved_lang': resolved_lang,
                            'streaming_capability': capability,
                        }
                    ),
                    flush=True,
                )
            elif cmd == 'transcribe':
                if model is None:
                    print(
                        json.dumps({'ok': False, 'error_code': 'MODEL_LOAD_FAILED', 'error': 'Modelo Nemotron não carregado'}),
                        flush=True,
                    )
                    continue
                wav_path = str(req['wav_path'])
                req_lang = _resolve_model_target_lang(model, str(req.get('language') or resolved_lang or 'pt'))
                mode = str(req.get('mode') or 'offline')
                t0 = time.perf_counter()
                if mode == 'streaming':
                    text, meta = _buffered_chunk_transcribe(
                        model,
                        wav_path,
                        req_lang,
                        chunk_ms=int(req.get('chunk_ms', 160)),
                        right_context_ms=int(req.get('right_context_ms', 400)),
                        left_context_ms=int(req.get('left_context_ms', 5600)),
                        capability=capability,
                    )
                else:
                    out = _call_transcribe(model, [wav_path], req_lang)
                    text = _extract_text(out)
                    meta = {'inference_mode': 'offline', 'chunks_processed': 1, 'first_partial_ms': None, 'streaming_note': None}
                inf_ms = (time.perf_counter() - t0) * 1000.0
                print(
                    json.dumps(
                        {
                            'ok': True,
                            'text': text,
                            'inference_ms': round(inf_ms, 3),
                            'rss_mb': _process_tree_rss_mb(os.getpid()),
                            **meta,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            elif cmd == 'unload':
                model = None
                gc.collect()
                print(
                    json.dumps(
                        {
                            'ok': True,
                            'model_loaded': False,
                            'rss_mb': _process_tree_rss_mb(os.getpid()),
                        }
                    ),
                    flush=True,
                )
            elif cmd == 'shutdown':
                model = None
                gc.collect()
                print(json.dumps({'ok': True}), flush=True)
                return 0
            else:
                print(json.dumps({'ok': False, 'error_code': 'MODEL_LOAD_FAILED', 'error': f'Comando desconhecido: {cmd}'}), flush=True)
        except MemoryError as exc:
            print(json.dumps({'ok': False, 'error_code': 'oom', 'error': f'OOM no worker Nemotron: {exc}'}), flush=True)
        except Exception as exc:
            code = getattr(exc, 'code', 'MODEL_LOAD_FAILED')
            if isinstance(exc, ImportError):
                code = 'MODEL_RUNTIME_UNAVAILABLE'
            print(json.dumps({'ok': False, 'error_code': code, 'error': str(exc)}), flush=True)
    return 0


if __name__ == '__main__' and '--worker' in sys.argv:
    raise SystemExit(_worker_main())
