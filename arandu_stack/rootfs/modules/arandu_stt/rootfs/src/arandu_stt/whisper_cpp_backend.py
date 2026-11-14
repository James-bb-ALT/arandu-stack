"""Persistent local whisper.cpp server backend with fixed Portuguese ('pt') inference."""
from __future__ import annotations

import atexit
from collections import deque
import http.client
import io
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
import wave

import numpy as np

from stt_errors import AranduRuntimeUnavailableError, AranduStructuredError

WHISPER_CPP_VERSION = '1.9.5'
WHISPER_CPP_COMMIT = 'd1be6fde11ac6e0407606b4e42fe72d34add8037'
FIXED_LANGUAGE = 'pt'
FIXED_TRANSLATE = False
FIXED_DETECT_LANGUAGE = False

MODEL_FILENAMES = {
    'whisper_cpp_small': 'ggml-small.bin',
    'whisper_cpp_medium': 'ggml-medium.bin',
}

DEFAULT_WHISPER_SERVER_PATHS = (
    Path('/usr/local/bin/whisper-server'),
    Path('/usr/bin/whisper-server'),
)

LFS_POINTER_PREFIX = b'version https://git-lfs.github.com/spec/v1'
MIN_GGML_BYTES = 1024


def find_whisper_server_binary() -> str | None:
    """Locate the compiled whisper-server executable without auto-downloading or compiling."""
    env_override = os.environ.get('ARANDU_WHISPER_SERVER_BIN')
    if env_override is not None:
        trimmed = env_override.strip()
        if not trimmed:
            return None
        cand = Path(trimmed)
        if cand.is_file() and (os.name == 'nt' or os.access(cand, os.X_OK)):
            return str(cand)
        return None
    for cand in DEFAULT_WHISPER_SERVER_PATHS:
        if cand.is_file() and (os.name == 'nt' or os.access(cand, os.X_OK)):
            return str(cand)
    found = shutil.which('whisper-server')
    return found


def is_whisper_cpp_runtime_available() -> bool:
    """Return True when the whisper-server binary is present and executable."""
    return bool(find_whisper_server_binary())


def validate_fixed_portuguese(
    language: str | None = FIXED_LANGUAGE,
    translate: bool = FIXED_TRANSLATE,
    detect_language: bool = FIXED_DETECT_LANGUAGE,
) -> str:
    """Enforce Portuguese-only ('pt') non-translating inference without auto-detection."""
    if not isinstance(language, str) or language.strip().lower() != FIXED_LANGUAGE:
        raise ValueError(
            "Idioma fixo em português ('pt'); alteração ou detecção automática ('auto') não é permitida."
        )
    if bool(translate):
        raise ValueError("Tradução automática não é permitida para whisper.cpp; translate deve permanecer false.")
    if bool(detect_language):
        raise ValueError("Detecção automática de idioma não é permitida para whisper.cpp.")
    return FIXED_LANGUAGE


def _allocate_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return int(sock.getsockname()[1])


def _audio_to_wav_bytes(audio) -> tuple[bytes, int]:
    """Convert 16 kHz mono audio (bytes PCM16 or float array/sequence) into WAV bytes."""
    if audio is None:
        return b'', 0
    if isinstance(audio, (bytes, bytearray, memoryview)):
        raw = bytes(audio)
        if not raw:
            return b'', 0
        if len(raw) % 2 != 0:
            raise ValueError('PCM16 inválido: número ímpar de bytes')
        pcm_bytes = raw
        sample_count = len(raw) // 2
    else:
        arr = np.asarray(audio)
        if arr.size == 0:
            return b'', 0
        if arr.ndim > 1:
            arr = arr.reshape(-1)
        if np.issubdtype(arr.dtype, np.integer):
            pcm16 = np.clip(arr, -32768, 32767).astype('<i2')
        else:
            pcm16 = np.clip(np.round(arr.astype(np.float32) * 32767.0), -32768, 32767).astype('<i2')
        pcm_bytes = pcm16.tobytes()
        sample_count = int(pcm16.size)
    if sample_count == 0 or not np.any(np.frombuffer(pcm_bytes, dtype='<i2')):
        return b'', 0
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(pcm_bytes)
    return buf.getvalue(), sample_count


def _build_multipart_payload(fields: dict[str, str], file_field: str, filename: str, content: bytes, mime: str) -> tuple[bytes, str]:
    boundary = 'AranduWhisperCpp' + uuid.uuid4().hex
    parts: list[bytes] = []
    for key, value in fields.items():
        parts.append(
            (
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
                f'{value}\r\n'
            ).encode('utf-8')
        )
    parts.append(
        (
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
            f'Content-Type: {mime}\r\n\r\n'
        ).encode('utf-8')
        + content
        + b'\r\n'
    )
    parts.append(f'--{boundary}--\r\n'.encode('utf-8'))
    return b''.join(parts), f'multipart/form-data; boundary={boundary}'


def _tail_implies_oom(lines: deque[str], returncode: int | None) -> bool:
    if returncode in (-9, 137):
        return True
    joined = ' '.join(lines).lower()
    return any(tok in joined for tok in ('out of memory', 'bad_alloc', 'cannot allocate memory', 'killed'))


class WhisperCppBackend:
    """Manage an isolated persistent whisper-server subprocess bound strictly to 127.0.0.1."""

    def __init__(
        self,
        model_dir: Path | str,
        threads: int = 2,
        *,
        model_id: str = 'whisper_cpp_small',
        language: str = FIXED_LANGUAGE,
        translate: bool = FIXED_TRANSLATE,
        detect_language: bool = FIXED_DETECT_LANGUAGE,
        load_timeout_sec: float | None = None,
        request_timeout_sec: float | None = None,
    ) -> None:
        if type(threads) is not int or not 1 <= threads <= 4:
            raise ValueError('Threads deve ficar entre 1 e 4')
        if model_id not in MODEL_FILENAMES:
            raise ValueError(f'Modelo whisper.cpp desconhecido: {model_id}')
        validate_fixed_portuguese(language, translate, detect_language)

        self.model_dir = Path(model_dir)
        self.threads = threads
        self.model_id = model_id
        self.filename = MODEL_FILENAMES[model_id]
        self.language = FIXED_LANGUAGE
        self.translate = FIXED_TRANSLATE
        self.detect_language = FIXED_DETECT_LANGUAGE
        default_timeout = 360.0 if model_id == 'whisper_cpp_medium' else 180.0
        self.load_timeout_sec = float(
            load_timeout_sec
            if load_timeout_sec is not None
            else os.environ.get('ARANDU_WHISPER_CPP_LOAD_TIMEOUT_SEC', '120')
        )
        self.request_timeout_sec = float(
            request_timeout_sec
            if request_timeout_sec is not None
            else os.environ.get('ARANDU_WHISPER_CPP_TIMEOUT_SEC', str(default_timeout))
        )

        self._proc: subprocess.Popen | None = None
        self._stderr_lines: deque[str] = deque(maxlen=80)
        self._stderr_thread: threading.Thread | None = None
        self._infer_lock = threading.Lock()
        self.port: int | None = None
        self.server_pid: int | None = None
        self.load_ms: float | None = None
        self._atexit_registered = False

    def _resolve_model_file(self) -> Path:
        model_path = self.model_dir / self.filename
        if not model_path.is_file():
            raise FileNotFoundError(f'Pesos GGML ausentes: {self.filename}')
        size = model_path.stat().st_size
        if size < MIN_GGML_BYTES:
            raise ValueError(f'Arquivo de pesos whisper.cpp incompleto ou corrompido: {self.filename}')
        with model_path.open('rb') as handle:
            head = handle.read(len(LFS_POINTER_PREFIX))
        if head.startswith(LFS_POINTER_PREFIX):
            raise ValueError(f'Arquivo de pesos whisper.cpp inválido (ponteiro Git LFS): {self.filename}')
        return model_path

    def _drain_stderr(self, pipe) -> None:
        try:
            for raw_line in iter(pipe.readline, b''):
                line = raw_line.decode('utf-8', errors='replace').strip()
                if line:
                    self._stderr_lines.append(line[:240])
        except Exception:
            pass
        finally:
            try:
                pipe.close()
            except Exception:
                pass

    def _check_health(self, port: int) -> bool:
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=1.0) as resp:
                if resp.status != 200:
                    return False
                data = json.loads(resp.read().decode('utf-8'))
                return isinstance(data, dict) and data.get('status') == 'ok'
        except (urllib.error.URLError, TimeoutError, ValueError, OSError, http.client.HTTPException):
            return False

    def load(self) -> 'WhisperCppBackend':
        if self._proc is not None and self._proc.poll() is None:
            return self
        self.close()
        self._stderr_lines.clear()

        model_path = self._resolve_model_file()
        binary = find_whisper_server_binary()
        if not binary:
            raise AranduRuntimeUnavailableError(
                f'Binário whisper-server (whisper.cpp v{WHISPER_CPP_VERSION}) não encontrado.'
            )

        port = _allocate_loopback_port()
        cmd = [
            binary,
            '--model', str(model_path),
            '--host', '127.0.0.1',
            '--port', str(port),
            '--threads', str(self.threads),
            '--language', FIXED_LANGUAGE,
            '--beam-size', '1',
            '--best-of', '1',
            '--max-context', '0',
            '--no-gpu',
            '--no-fallback',
            '--no-timestamps',
        ]
        started = time.perf_counter()
        popen_kwargs: dict[str, object] = {
            'stdin': subprocess.DEVNULL,
            'stdout': subprocess.DEVNULL,
            'stderr': subprocess.PIPE,
            'close_fds': True,
        }
        if os.name == 'posix':
            popen_kwargs['start_new_session'] = True

        proc = subprocess.Popen(cmd, **popen_kwargs)
        self._proc = proc
        self.port = port
        self.server_pid = proc.pid
        if proc.stderr is not None:
            self._stderr_thread = threading.Thread(
                target=self._drain_stderr, args=(proc.stderr,), daemon=True, name='whisper-cpp-stderr'
            )
            self._stderr_thread.start()

        if not self._atexit_registered:
            atexit.register(self.close)
            self._atexit_registered = True

        deadline = time.monotonic() + self.load_timeout_sec
        try:
            while time.monotonic() < deadline:
                rc = proc.poll()
                if rc is not None:
                    if _tail_implies_oom(self._stderr_lines, rc):
                        raise MemoryError(f'whisper-server encerrado por falta de memória (exit={rc})')
                    tail = ' | '.join(list(self._stderr_lines)[-5:])
                    raise AranduStructuredError(
                        'MODEL_LOAD_FAILED',
                        f'whisper-server encerrou durante carga (exit={rc})' + (f': {tail}' if tail else ''),
                        stage='load',
                    )
                if self._check_health(port):
                    self.load_ms = (time.perf_counter() - started) * 1000.0
                    return self
                time.sleep(0.05)
            raise TimeoutError(f'Timeout após {self.load_timeout_sec:.0f}s aguardando whisper-server carregar {self.filename}')
        except BaseException:
            self.close()
            raise

    def _post_inference(self, wav_bytes: bytes) -> dict:
        if self.port is None:
            raise RuntimeError('Porta de inferência whisper-server não inicializada')
        body, content_type = _build_multipart_payload(
            fields={
                'language': FIXED_LANGUAGE,
                'translate': 'false',
                'detect_language': 'false',
                'beam_size': '1',
                'best_of': '1',
                'temperature': '0.0',
                'temperature_inc': '0.0',
                'no_timestamps': 'true',
                'no_language_probabilities': 'true',
                'response_format': 'json',
            },
            file_field='file',
            filename='audio.wav',
            content=wav_bytes,
            mime='audio/wav',
        )
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=self.request_timeout_sec)
        try:
            conn.request(
                'POST',
                '/inference',
                body=body,
                headers={
                    'Content-Type': content_type,
                    'Content-Length': str(len(body)),
                },
            )
            resp = conn.getresponse()
            raw = resp.read()
        finally:
            conn.close()

        if resp.status != 200:
            snippet = raw.decode('utf-8', errors='replace')[:200]
            raise RuntimeError(f'whisper-server retornou HTTP {resp.status}: {snippet}')
        try:
            data = json.loads(raw.decode('utf-8'))
        except ValueError as exc:
            raise RuntimeError('Resposta JSON inválida do whisper-server') from exc
        if not isinstance(data, dict) or 'text' not in data:
            if isinstance(data, dict) and data.get('error'):
                raise RuntimeError(f"whisper-server erro: {data['error']}")
            raise RuntimeError('Resposta do whisper-server sem campo text')
        return data

    def recognize(
        self,
        audio,
        *,
        language: str = FIXED_LANGUAGE,
        translate: bool = FIXED_TRANSLATE,
        detect_language: bool = FIXED_DETECT_LANGUAGE,
    ) -> tuple[str, float]:
        validate_fixed_portuguese(language, translate, detect_language)
        with self._infer_lock:
            if self._proc is None or self._proc.poll() is not None or self.port is None:
                raise RuntimeError('Modelo whisper.cpp não está carregado ou o servidor foi encerrado')
            wav_bytes, sample_count = _audio_to_wav_bytes(audio)
            if sample_count == 0 or not wav_bytes:
                return '', 0.0
            start = time.perf_counter()
            try:
                data = self._post_inference(wav_bytes)
            except (socket.timeout, TimeoutError) as exc:
                raise TimeoutError(f'Timeout após {self.request_timeout_sec:.0f}s na inferência whisper.cpp') from exc
            except (http.client.HTTPException, OSError) as exc:
                rc = self._proc.poll() if self._proc is not None else None
                if _tail_implies_oom(self._stderr_lines, rc):
                    raise MemoryError(f'whisper-server encerrado por falta de memória durante inferência (exit={rc})') from exc
                raise RuntimeError(f'Falha de comunicação com whisper-server (exit={rc}): {exc}') from exc
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            text = ' '.join(str(data.get('text') or '').strip().split())
            return text, elapsed_ms

    def close(self) -> None:
        proc = self._proc
        self._proc = None
        self.port = None
        self.server_pid = None
        if proc is None:
            return
        descendants = []
        try:
            import psutil
            parent = psutil.Process(proc.pid)
            descendants = parent.children(recursive=True)
        except Exception:
            descendants = []
        try:
            for child in reversed(descendants):
                try:
                    child.terminate()
                except Exception:
                    pass
            if proc.poll() is None:
                if os.name == 'posix':
                    try:
                        os.killpg(proc.pid, signal.SIGTERM)
                    except OSError:
                        proc.terminate()
                else:
                    proc.terminate()
                try:
                    proc.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    for child in reversed(descendants):
                        try:
                            child.kill()
                        except Exception:
                            pass
                    if os.name == 'posix':
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except OSError:
                            proc.kill()
                    else:
                        proc.kill()
                    proc.wait(timeout=2.0)
            for child in descendants:
                try:
                    if child.is_running():
                        child.kill()
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            thread = self._stderr_thread
            self._stderr_thread = None
            if thread is not None and thread.is_alive():
                thread.join(timeout=1.0)
            if proc.stderr is not None and (thread is None or not thread.is_alive()):
                try:
                    proc.stderr.close()
                except Exception:
                    pass
