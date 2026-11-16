"""Control-plane STT operations. Temporary engines never replace the Assist engine."""
from __future__ import annotations

import asyncio
import base64
from collections import deque
import copy
import hashlib
import inspect
import json
import os
from pathlib import Path
import sys
import tempfile
import time

from aiohttp import ClientSession, ClientTimeout

SOURCE = Path('/opt/modules/arandu_stt/rootfs/src/arandu_stt')
if not SOURCE.exists():
    SOURCE = Path(__file__).resolve().parents[1] / 'rootfs/modules/arandu_stt/rootfs/src/arandu_stt'
sys.path.insert(0, str(SOURCE))
from memory_budget import classify_memory_failure, compute_memory_budget
from model_catalog import CATALOG, describe, invalidate_runtime_cache, validate

MAX_UPLOAD = 1_400_000  # Keep the Base64 JSON envelope below the shared 2 MiB request budget.
MAX_PCM = 12 * 16000 * 2


class WorkerExecutionError(ValueError):
    def __init__(self, message: str, error_code: str | None = None, failure_detail: dict | None = None):
        super().__init__(message)
        self.error_code = error_code
        self.failure_detail = failure_detail


def _descendant_pids(root_pid: int) -> list[int]:
    pids = [root_pid]
    seen = {root_pid}
    queue = [root_pid]
    while queue:
        current = queue.pop(0)
        for children_file in Path(f'/proc/{current}/task').glob('*/children'):
            try:
                for token in children_file.read_text(encoding='utf-8').split():
                    child = int(token)
                    if child not in seen:
                        seen.add(child)
                        pids.append(child)
                        queue.append(child)
            except (OSError, ValueError):
                pass
    return pids


def _tree_rss_bytes(root_pid: int) -> int | None:
    total = 0
    found = False
    for pid in _descendant_pids(root_pid):
        try:
            status = Path(f'/proc/{pid}/status').read_text(encoding='utf-8')
            rss = next(int(line.split()[1]) for line in status.splitlines() if line.startswith('VmRSS:')) * 1024
            total += rss
            found = True
        except (OSError, StopIteration, ValueError):
            pass
    return total if found else None


def _kill_process_tree(process) -> None:
    pid = getattr(process, 'pid', None)
    if pid:
        for child_pid in reversed(_descendant_pids(pid)):
            if child_pid != pid:
                try:
                    os.kill(child_pid, 9)
                except OSError:
                    pass
    if getattr(process, 'returncode', None) is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass


async def watch_memory(process, limit, oom_flag=None):
    while process.returncode is None and getattr(process, 'pid', None):
        rss = _tree_rss_bytes(process.pid)
        if rss is not None and rss > limit:
            if oom_flag is not None:
                oom_flag.set()
            _kill_process_tree(process)
            return
        await asyncio.sleep(.2)


async def decode_audio(raw):
    if not 1 <= len(raw) <= MAX_UPLOAD:
        raise ValueError('Áudio vazio ou excedeu 1,4 MB')
    if not (raw.startswith((b'RIFF', b'OggS', b'\x1aE\xdf\xa3')) or raw[4:8] == b'ftyp'):
        raise ValueError('Formato inválido. Use WAV, WebM/Opus, Ogg ou MP4/AAC')
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix='arandu-audio-') as folder:
        source = Path(folder) / 'input.audio'
        source.write_bytes(raw)
        process = await asyncio.create_subprocess_exec(
            'ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-threads', '1',
            '-filter_threads', '1', '-max_alloc', '67108864', '-protocol_whitelist', 'file,pipe',
            '-i', str(source), '-map', '0:a:0', '-vn', '-t', '12.05', '-ac', '1', '-ar', '16000',
            '-f', 's16le', 'pipe:1', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        monitor = asyncio.create_task(watch_memory(process, 512 * 1024**2))
        try:
            pcm, _ = await asyncio.wait_for(process.communicate(), 15)
            if process.returncode or not 2 <= len(pcm) <= MAX_PCM or len(pcm) % 2:
                raise ValueError('Áudio inválido, vazio ou duração superior a 12 segundos')
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        finally:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
    return pcm, (time.perf_counter() - started) * 1000


class STTLab:
    def __init__(self, manager):
        self.manager = manager
        self.root = manager.data / 'modules/stt/models'
        self.busy = asyncio.Lock()
        self.operations = {}
        self.validation_state = {}
        self.install_task = None
        self.children = set()
        self.cancel_requested = False
        self._session_proc = None
        self._session_stderr = deque(maxlen=50)
        self._session_stderr_task = None
        self._retention_task = None
        self.retention_seconds = 900
        self.session_state = 'unloaded'
        self.session_info = {}
        self.session_history = []

    def _cancel_retention(self):
        if self._retention_task and not self._retention_task.done():
            self._retention_task.cancel()
        self._retention_task = None

    def _schedule_retention(self, seconds):
        self._cancel_retention()
        if seconds is None:
            return
        try:
            sec = int(seconds)
        except (TypeError, ValueError):
            return
        if sec <= 0:
            return

        async def _expire():
            try:
                await asyncio.sleep(sec)
                async with self.busy:
                    await self._unload_session_locked()
            except asyncio.CancelledError:
                pass

        self._retention_task = asyncio.create_task(_expire())

    def _build_worker_env(self, threads: int = 2) -> dict[str, str]:
        environment = {
            key: value for key, value in os.environ.items()
            if not any(secret in key.upper() for secret in ('TOKEN', 'SECRET', 'PASSWORD', 'API_KEY', 'ACCESS_KEY', 'CREDENTIAL'))
        }
        environment.update(
            OMP_NUM_THREADS=str(threads),
            OPENBLAS_NUM_THREADS='1',
            MKL_NUM_THREADS=str(threads),
            HF_HUB_DISABLE_TELEMETRY='1',
            PYTHONIOENCODING='utf-8',
        )
        return environment

    async def _kill_session_proc(self):
        proc = self._session_proc
        self._session_proc = None
        if self._session_stderr_task and not self._session_stderr_task.done():
            self._session_stderr_task.cancel()
            await asyncio.gather(self._session_stderr_task, return_exceptions=True)
        self._session_stderr_task = None
        if proc is not None:
            _kill_process_tree(proc)
            try:
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except Exception:
                pass
            self.children.discard(proc)

    async def _ensure_session_proc(self, threads: int = 2):
        if self._session_proc is not None and self._session_proc.returncode is None:
            return self._session_proc
        await self._kill_session_proc()
        python = os.environ.get('ARANDU_STT_PYTHON', '/opt/venvs/stt/bin/python')
        self._session_stderr.clear()
        proc = await asyncio.create_subprocess_exec(
            python,
            str(SOURCE / 'lab_worker.py'),
            '--serve',
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._build_worker_env(threads),
        )
        self._session_proc = proc
        self.children.add(proc)

        async def _drain():
            try:
                while proc.stderr is not None:
                    line = await proc.stderr.readline()
                    if not line:
                        break
                    txt = line.decode('utf-8', errors='replace').strip()
                    if txt:
                        self._session_stderr.append(txt)
            except Exception:
                pass

        self._session_stderr_task = asyncio.create_task(_drain())
        return proc

    async def _session_rpc(self, payload: dict, timeout: float = 120.0) -> dict:
        threads = int(payload.get('threads', 2) or 2)
        proc = await self._ensure_session_proc(threads)
        assert proc.stdin is not None and proc.stdout is not None
        encoded = (json.dumps({**payload, 'root': str(self.root)}) + '\n').encode('utf-8')
        try:
            proc.stdin.write(encoded)
            await proc.stdin.drain()
            while True:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
                if not line:
                    try:
                        rc = await asyncio.wait_for(proc.wait(), timeout=2.0)
                    except asyncio.TimeoutError:
                        rc = proc.returncode
                    if self.cancel_requested:
                        await self._kill_session_proc()
                        self.session_state = 'unloaded'
                        raise WorkerExecutionError('Operação cancelada pelo usuário', 'cancelled')
                    detail = classify_memory_failure(
                        returncode=rc,
                        stderr_lines=list(self._session_stderr),
                        internal_limit_mb=payload.get('memory_limit_mb'),
                        stage=payload.get('cmd', 'infer'),
                    )
                    await self._kill_session_proc()
                    self.session_state = 'error'
                    self.session_info['last_error'] = detail['reason']
                    self.session_info['failure_detail'] = detail
                    raise WorkerExecutionError(detail['reason'], detail['error_code'], failure_detail=detail)
                text = line.decode('utf-8', errors='replace').strip()
                if text.startswith('{') and text.endswith('}'):
                    resp = json.loads(text)
                    if not resp.get('ok'):
                        err_code = resp.get('error_code', 'execution_error')
                        msg = resp.get('message', 'Falha na sessão STT')
                        fdetail = resp.get('failure_detail')
                        if fdetail:
                            self.session_info['failure_detail'] = fdetail
                        if err_code == 'oom' or resp.get('state') == 'error':
                            self.session_state = 'error'
                            self.session_info['last_error'] = msg
                        raise WorkerExecutionError(msg, err_code, failure_detail=fdetail)
                    return resp
        except asyncio.TimeoutError as exc:
            await self._kill_session_proc()
            self.session_state = 'error'
            self.session_info['last_error'] = 'Tempo limite excedido na sessão residente STT'
            raise TimeoutError('Tempo limite excedido na sessão residente STT') from exc
        except WorkerExecutionError:
            raise
        except Exception as exc:
            await self._kill_session_proc()
            self.session_state = 'error'
            self.session_info['last_error'] = str(exc)
            raise WorkerExecutionError(str(exc), 'execution_error') from exc

    async def _unload_session_locked(self) -> dict:
        self._cancel_retention()
        if self._session_proc is None or self._session_proc.returncode is not None:
            await self._kill_session_proc()
            self.session_state = 'unloaded'
            self.session_info.update({'state': 'unloaded', 'model': None, 'process_rss_mb': 0.0})
            return {'ok': True, 'state': 'unloaded', 'freed_mb': 0.0}
        try:
            resp = await self._session_rpc({'cmd': 'unload'}, timeout=15.0)
        except Exception:
            await self._kill_session_proc()
            resp = {'ok': True, 'state': 'unloaded', 'freed_mb': 0.0}
        self.session_state = 'unloaded'
        self.session_info.update({'state': 'unloaded', 'model': None, 'process_rss_mb': resp.get('ram_after_mb', 0.0)})
        return resp

    async def session_status(self, *, memory_policy: str = 'auto', memory_limit_mb=None) -> dict:
        active = await self.active()
        prod_rss = float((active or {}).get('process_rss_mb') or 0.0)
        if self._session_proc is not None and self._session_proc.returncode is None and not self.busy.locked():
            try:
                resp = await self._session_rpc({
                    'cmd': 'status',
                    'memory_policy': memory_policy,
                    'memory_limit_mb': memory_limit_mb,
                    'production_stt_rss_mb': prod_rss,
                }, timeout=10.0)
                self.session_state = resp.get('state', self.session_state)
                self.session_info = resp
                return {
                    **resp,
                    'retention_seconds': self.retention_seconds,
                    'history': list(self.session_history[-25:]),
                    'production_active': active,
                }
            except Exception:
                pass
        elif self._session_proc is not None and self._session_proc.returncode is not None and self.session_state not in ('unloaded', 'error'):
            detail = classify_memory_failure(
                returncode=self._session_proc.returncode,
                stderr_lines=list(self._session_stderr),
                internal_limit_mb=self.session_info.get('memory_budget', {}).get('memory_limit_mb'),
                stage='resident',
            )
            await self._kill_session_proc()
            self.session_state = 'error'
            self.session_info['last_error'] = detail['reason']
            self.session_info['failure_detail'] = detail
        budget = compute_memory_budget(
            policy=memory_policy,
            custom_limit_mb=memory_limit_mb,
            production_stt_rss_mb=prod_rss,
        )
        return {
            'ok': True,
            'state': self.session_state,
            'model': self.session_info.get('model'),
            'variant': self.session_info.get('variant'),
            'threads': self.session_info.get('threads'),
            'cold_load_ms': self.session_info.get('cold_load_ms'),
            'warmup_ms': self.session_info.get('warmup_ms'),
            'warmed_up': self.session_info.get('warmed_up', False),
            'infer_count': self.session_info.get('infer_count', 0),
            'process_rss_mb': self.session_info.get('process_rss_mb', 0.0),
            'memory_budget': self.session_info.get('memory_budget') or budget,
            'retention_seconds': self.retention_seconds,
            'last_error': self.session_info.get('last_error'),
            'failure_detail': self.session_info.get('failure_detail'),
            'history': list(self.session_history[-25:]),
            'production_active': active,
        }

    async def load_model(self, body: dict) -> dict:
        model, variant, threads = self.parameters(body)
        if self.busy.locked():
            raise ValueError('STT ocupado; aguarde a operação atual')
        memory_policy = body.get('memory_policy', 'auto')
        memory_limit_mb = body.get('memory_limit_mb')
        if 'retention_seconds' in body:
            self.retention_seconds = body.get('retention_seconds')
        async with self.busy:
            self.cancel_requested = False
            self.session_state = 'loading'
            active = await self.active()
            prod_rss = float((active or {}).get('process_rss_mb') or 0.0)
            timeout = self._model_timeout(model, 1)
            resp = await self._session_rpc({
                'cmd': 'load',
                'model': model,
                'variant': variant,
                'threads': threads,
                'warmup': bool(body.get('warmup', False)),
                'memory_policy': memory_policy,
                'memory_limit_mb': memory_limit_mb,
                'nemotron_streaming_profile': body.get('nemotron_streaming_profile') or body.get('nemotron_profile'),
                'production_stt_rss_mb': prod_rss,
            }, timeout=timeout)
            self.session_state = 'resident'
            self.session_info = resp
            self._schedule_retention(self.retention_seconds)
            return {**resp, 'retention_seconds': self.retention_seconds, 'history': list(self.session_history[-25:])}

    async def warmup_model(self, body: dict | None = None) -> dict:
        if self.busy.locked():
            raise ValueError('STT ocupado; aguarde a operação atual')
        async with self.busy:
            self.cancel_requested = False
            self.session_state = 'warming'
            resp = await self._session_rpc({'cmd': 'warmup'}, timeout=120.0)
            self.session_state = 'resident'
            self.session_info = resp
            self._schedule_retention(self.retention_seconds)
            return {**resp, 'retention_seconds': self.retention_seconds}

    async def unload_model(self) -> dict:
        if self.busy.locked():
            raise ValueError('STT ocupado; aguarde a operação atual')
        async with self.busy:
            return await self._unload_session_locked()

    async def cancel(self):
        self.cancel_requested = True
        self._cancel_retention()
        active_children = list(self.children)
        for proc in active_children:
            _kill_process_tree(proc)
        self._session_proc = None
        self.session_state = 'unloaded'
        return {'cancelled': True, 'state': 'cancelled', 'stopped_workers': len(active_children)}

    async def worker(self, body, timeout=90):
        python = os.environ.get('ARANDU_STT_PYTHON', '/opt/venvs/stt/bin/python')
        environment = {key: value for key, value in os.environ.items()
                       if not any(secret in key.upper() for secret in ('TOKEN', 'SECRET', 'PASSWORD', 'API_KEY', 'ACCESS_KEY', 'CREDENTIAL'))}
        environment.update(OMP_NUM_THREADS=str(body.get('threads', 2)), OPENBLAS_NUM_THREADS='1',
                           MKL_NUM_THREADS=str(body.get('threads', 2)), HF_HUB_DISABLE_TELEMETRY='1', PYTHONIOENCODING='utf-8')
        process = await asyncio.create_subprocess_exec(python, str(SOURCE/'lab_worker.py'),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            env=environment)
        self.children.add(process)
        oom_flag = asyncio.Event()
        monitor = asyncio.create_task(watch_memory(process, CATALOG[body.get('model', 'fastconformer')]['memory_limit_mb'] * 1024**2, oom_flag=oom_flag))
        try:
            raw, _ = await asyncio.wait_for(process.communicate(json.dumps({**body, 'root': str(self.root)}).encode()), timeout)
            if len(raw) > 2_097_152:
                raise WorkerExecutionError('Resposta STT excedeu o limite', 'execution_error')
            if not raw:
                if self.cancel_requested:
                    raise WorkerExecutionError('Benchmark cancelado pelo usuário', 'cancelled')
                if oom_flag.is_set() or process.returncode in (-9, 137):
                    raise WorkerExecutionError('Modelo excedeu o limite de memória RAM (OOM).', 'oom')
                raise WorkerExecutionError('Falha no processo STT', 'execution_error')
            result = json.loads(raw)
            if process.returncode or result.get('error'):
                raise WorkerExecutionError(result.get('message', 'Falha no processo STT'), result.get('error_code'))
            return result
        except BaseException:
            _kill_process_tree(process)
            await process.wait()
            raise
        finally:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
            self.children.discard(process)

    async def active(self):
        try:
            async with ClientSession(timeout=ClientTimeout(total=2), trust_env=False) as session:
                async with session.get('http://127.0.0.1:11560/status', allow_redirects=False) as response:
                    if response.status == 200:
                        return await response.json()
        except (OSError, TimeoutError, asyncio.TimeoutError):
            pass
        return None

    async def models(self, variant=None):
        variant = variant or self.manager.config['stt']['model_variant']
        validate('fastconformer', variant)
        rows = [describe(self.root, model, variant) for model in CATALOG]
        active = await self.active()
        process = self.manager.processes.get('stt')
        loading = active is None and process is not None and process.returncode is None
        for row in rows:
            key = (row['id'], variant)
            if row['installed'] and key in self.validation_state:
                row.update(self.validation_state[key])
            if loading and row['id'] == self.manager.config['stt']['stt_model'] and row['installed'] and not row.get('incomplete') and row.get('state') != 'error':
                row['state'] = 'loading'
                row['load_status'] = 'loading'
            if active is None and row['id'] == self.manager.config['stt']['stt_model'] and 'stt' in self.manager.failures:
                row.update(state='error', load_status='model_load_failed', error='Falha no processo STT; consulte os logs e verifique os pesos.')
            row.update(self.operations.get(key, {}))
            if row['state'] in ('downloading', 'verifying'):
                row['incomplete'] = False
                row.pop('error', None)
                row['weights_status'] = row['state']
                progress = self.root / ('.install-' + row['id'] + '.json')
                try:
                    payload = json.loads(progress.read_text(encoding='utf-8'))
                    if payload.get('state') == 'verifying':
                        row['state'] = 'verifying'
                        row['weights_status'] = 'verifying'
                    for field in ('bytes_downloaded', 'total_bytes', 'percent', 'speed_mbps', 'phase'):
                        if field in payload and payload[field] is not None:
                            row[field] = payload[field]
                except (OSError, ValueError):
                    pass
            if active and row['id'] == active.get('configured_model', active.get('model')) and (row['id'] != 'fastconformer' or variant == active.get('variant')):
                if active.get('model_loaded') is True:
                    row.update(state='ready', ready=True, active=True, load_status='model_ready')
                else:
                    act_state = active.get('state', 'waiting_first_use')
                    load_st = 'unloaded'
                    if act_state == 'loading':
                        load_st = 'loading'
                    elif act_state in ('load_failed', 'degraded') or active.get('last_load_error'):
                        err_txt = str(active.get('last_load_error') or '').lower()
                        load_st = 'oom' if ('oom' in err_txt or 'ram' in err_txt or 'mem' in err_txt) else ('timeout' if 'timeout' in err_txt else 'model_load_failed')
                    row.update(state=act_state, ready=False, active=False, load_status=load_st)
            if row.get('runtime_status') == 'runtime_ready' and not row.get('installed') and row.get('weights_status') != 'model_corrupted':
                row['runtime_note'] = None
                row['error_code'] = None
            if row.get('runtime_status') and row.get('runtime_status') != 'runtime_ready':
                row['diagnostic_state'] = row['runtime_status']
            elif row.get('weights_status') == 'model_corrupted':
                row['diagnostic_state'] = 'model_corrupted'
            elif not row.get('installed'):
                row['diagnostic_state'] = 'model_not_installed'
            elif row.get('load_status') in ('model_load_failed', 'oom', 'timeout'):
                row['diagnostic_state'] = row['load_status']
            elif row.get('load_status') == 'model_ready' or row.get('ready'):
                row['diagnostic_state'] = 'model_ready'
            else:
                row['diagnostic_state'] = 'runtime_ready'
        return {
            'models': rows,
            'active': active,
            'configured': self.manager.config['stt']['stt_model'],
            'configured_variant': self.manager.config['stt']['model_variant'],
            'resident_policy': (active or {}).get('resident_policy', self.manager.config['stt'].get('resident_policy', 'RESIDENT')),
            'model_loaded': bool(active and active.get('model_loaded') is True),
            'state': (active or {}).get('state', 'stopped' if active is None else 'unknown'),
            'load_ms': (active or {}).get('load_ms'),
            'cold_start_count': (active or {}).get('cold_start_count', 0),
            'process_rss_mb': (active or {}).get('process_rss_mb'),
            'last_load_error': (active or {}).get('last_load_error'),
        }

    def start_install(self, model, variant):
        validate(model, variant)
        if self.busy.locked() or self.install_task and not self.install_task.done():
            raise ValueError('STT ocupado; aguarde a operação atual')
        invalidate_runtime_cache(model)
        key = (model, variant)
        (self.root / ('.install-' + model + '.json')).unlink(missing_ok=True)
        self.operations[key] = {'state': 'downloading', 'weights_status': 'downloading'}
        async def install_one():
            async with self.busy:
                try:
                    install_timeout = 1800 if (CATALOG[model].get('download_bytes') or 0) >= 1_000_000_000 else 900
                    await self.worker({'command': 'install', 'model': model, 'variant': variant}, install_timeout)
                    invalidate_runtime_cache(model)
                    self.operations[key] = {'state': 'installed', 'weights_status': 'model_ready', 'incomplete': False}
                    self.validation_state[key] = {'load_status': 'unloaded'}
                except Exception as exc:
                    invalidate_runtime_cache(model)
                    err_code = getattr(exc, 'error_code', None)
                    detail = str(exc) if isinstance(exc, ValueError) else ('Tempo limite excedido' if isinstance(exc, TimeoutError) else 'Runtime STT ou rede indisponível')
                    self.operations[key] = {
                        'state': 'error',
                        'incomplete': True,
                        'weights_status': 'model_corrupted' if err_code == 'model_corrupted' else 'model_not_installed',
                        'error_code': err_code,
                        'error': detail + ' Cache anterior preservado; tente novamente.',
                    }
                    if err_code in ('oom', 'timeout'):
                        self.validation_state[key] = {'load_status': err_code}
                    elif err_code == 'model_corrupted':
                        self.operations[key]['weights_status'] = 'model_corrupted'
                    else:
                        self.validation_state[key] = {'load_status': 'model_load_failed'}
        self.install_task = asyncio.create_task(install_one())
        return {'state': 'downloading', 'model': model}

    async def remove(self, model, variant='adapted'):
        validate(model, variant)
        if self.busy.locked():
            raise ValueError('STT ocupado; aguarde a operação atual')
        active = await self.active()
        if active and active.get('active_model') == model and active.get('model_loaded'):
            raise ValueError('Não é possível remover o modelo ativo em RAM; troque ou desative antes')
        invalidate_runtime_cache(model)
        async with self.busy:
            result = await self.worker({'command': 'remove', 'model': model, 'variant': variant}, 60)
            invalidate_runtime_cache(model)
            self.operations.pop((model, variant), None)
            self.validation_state.pop((model, variant), None)
            return result

    def parameters(self, body):
        if body.get('language') not in (None, 'pt') or body.get('translate') is True or body.get('detect_language') is True:
            raise ValueError('Idioma fixo em português (pt); tradução e detecção automática não são permitidas')
        repetitions = body.get('repetitions', 1)
        if isinstance(repetitions, bool) or not isinstance(repetitions, int) or repetitions not in (1, 3, 5, 10):
            raise ValueError('Repetições inválidas; escolha 1, 3, 5 ou 10')
        model, variant = body.get('model', self.manager.config['stt']['stt_model']), body.get('variant', 'adapted')
        validate(model, variant)
        threads = body.get('threads', self.manager.config['stt']['threads'])
        profile = body.get('profile')
        if type(threads) is not int or not 1 <= threads <= 4 or profile not in (None, 'raw', 'normalize_only', 'full'):
            raise ValueError('Threads ou perfil STT inválidos')
        return model, variant, threads

    @staticmethod
    def _model_timeout(model: str, repetitions: int) -> int:
        if model.startswith('whisper_cpp_medium'):
            return min(600, 180 + 60 * repetitions)
        if model == 'nemotron_unified_0_6b':
            return min(600, 210 + 45 * repetitions)
        if model.startswith('whisper_cpp_small') or model in ('parakeet_ptbr_30s', 'parakeet_tagarela', 'parakeet_ultra_q8', 'parakeet_ultra_q4'):
            return min(420, 120 + 35 * repetitions)
        return min(300, 90 + 25 * (repetitions - 1))

    async def test(self, body, compare=False):
        model, variant, threads = self.parameters(body)
        repetitions = body.get('repetitions', 1)
        warmup = body.get('warmup', repetitions > 1)
        models = body.get('models', [model]) if compare else [model]
        max_models = len(CATALOG)
        if type(models) is not list or not 1 <= len(models) <= max_models or any(type(item) is not str for item in models) or len(set(models)) != len(models):
            raise ValueError(f'Selecione de 1 a {max_models} modelos distintos')
        for selected in models:
            validate(selected, variant)
        reference = body.get('reference', '')
        if type(reference) is not str or len(reference) > 512:
            raise ValueError('Referência deve ter até 512 caracteres')
        entity_reference = body.get('entity_reference', '')
        if type(entity_reference) is not str or len(entity_reference) > 128:
            raise ValueError('Entidade de referência deve ter até 128 caracteres')
        encoded = body.get('audio')
        if type(encoded) is not str or len(encoded) > 1_866_668:
            raise ValueError('Áudio excedeu o limite')
        if self.busy.locked():
            raise ValueError('STT ocupado; tente novamente após a operação atual')
        resident = bool(body.get('resident', True))
        execution_mode = body.get('execution_mode')
        memory_policy = body.get('memory_policy', 'auto')
        memory_limit_mb = body.get('memory_limit_mb')
        if 'retention_seconds' in body:
            self.retention_seconds = body.get('retention_seconds')
        worker_is_native = getattr(type(self), 'worker', None) is getattr(self.worker, '__func__', None)
        use_persistent = (not compare) and worker_is_native and (resident or execution_mode in ('warm', 'warmup_benchmark'))

        async with self.busy:
            self.cancel_requested = False
            started = time.perf_counter()
            pcm, decode_ms = await decode_audio(base64.b64decode(encoded, validate=True))
            rows = []
            digest = hashlib.sha256(pcm).hexdigest()
            active_prod = await self.active()
            prod_rss = float((active_prod or {}).get('process_rss_mb') or 0.0)
            if compare and self._session_proc is not None and self._session_proc.returncode is None:
                await self._unload_session_locked()
            sig = inspect.signature(self.worker)
            supports_timeout = (
                any(p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD) for p in sig.parameters.values())
                or len(sig.parameters) >= 2
                or 'timeout' in sig.parameters
            )
            for selected in models:
                if self.cancel_requested:
                    msg = 'Benchmark cancelado pelo usuário'
                    rows.append({'model': selected, 'state': 'cancelled', 'error_code': 'cancelled', 'error': msg, 'audio_sha256': digest})
                    self.operations[(selected, variant)] = {'state': 'error', 'error': msg}
                    continue
                item_started = time.perf_counter()
                self.operations[(selected, variant)] = {'state': 'loading'}
                model_timeout = self._model_timeout(selected, repetitions)
                worker_payload = {
                    'command': 'test', 'model': selected, 'variant': variant,
                    'threads': threads, 'profile': body.get('profile'), 'reference': reference, 'entity_reference': entity_reference,
                    'preset': body.get('preset', 'original'),
                    'audio_processing': body.get('audio_processing') if isinstance(body.get('audio_processing'), dict) else None,
                    'nemotron_streaming_profile': body.get('nemotron_streaming_profile') or body.get('nemotron_profile'),
                    'repetitions': repetitions, 'warmup': warmup,
                    'execution_mode': execution_mode, 'resident': resident,
                    'memory_policy': memory_policy, 'memory_limit_mb': memory_limit_mb,
                    'production_stt_rss_mb': prod_rss,
                    'options': self.manager.config['stt'], 'pcm': base64.b64encode(pcm).decode(),
                    'inventory': str(self.root.parent/'arandu_ha_inventory.json'),
                }
                try:
                    if use_persistent:
                        self.session_state = 'inferring'
                        row = await self._session_rpc({**worker_payload, 'cmd': 'test'}, timeout=model_timeout)
                        self.session_state = 'resident' if row.get('resident_after_test', True) else 'unloaded'
                        self.session_info.update({
                            'state': self.session_state,
                            'model': selected if self.session_state == 'resident' else None,
                            'variant': variant if (self.session_state == 'resident' and selected == 'fastconformer') else None,
                            'threads': threads if self.session_state == 'resident' else None,
                            'process_rss_mb': row.get('process_rss_mb', 0.0),
                            'memory_budget': row.get('memory_budget'),
                        })
                        if self.session_state == 'resident':
                            self._schedule_retention(self.retention_seconds)
                    else:
                        row = await (self.worker(worker_payload, model_timeout) if supports_timeout else self.worker(worker_payload))
                    elapsed_total = (time.perf_counter()-item_started)*1000
                    row.update(
                        audio_decode_ms=decode_ms,
                        audio_sha256=digest,
                        request_total_ms=elapsed_total,
                        total_ms=row.get('total_ms') if row.get('total_ms') is not None else elapsed_total,
                        error_code=None,
                    )
                    rows.append(row)
                    self.operations.pop((selected, variant), None)
                    self.validation_state[(selected, variant)] = {
                        'load_status': 'model_ready',
                        'load_ms': row.get('cold_load_ms') or row.get('model_load_ms'),
                        'process_rss_mb': row.get('process_rss_mb'),
                    }
                    self.session_history.append({
                        'model': selected,
                        'threads': threads,
                        'execution_mode': row.get('execution_mode'),
                        'session_reused': row.get('session_reused', False),
                        'cold_load_ms': row.get('cold_load_ms'),
                        'inference_ms': row.get('inference_ms'),
                        'process_rss_mb': row.get('process_rss_mb'),
                        'realtime_factor': row.get('realtime_factor'),
                    })
                    self.session_history = self.session_history[-50:]
                except (ValueError, TimeoutError, OSError) as exc:
                    raw_msg = str(exc)
                    explicit_code = getattr(exc, 'error_code', None)
                    failure_detail = getattr(exc, 'failure_detail', None)
                    if self.cancel_requested or explicit_code == 'cancelled':
                        err_code = 'cancelled'
                        message = 'Benchmark cancelado pelo usuário'
                    elif isinstance(exc, TimeoutError) or explicit_code == 'timeout':
                        err_code = 'timeout'
                        message = 'Tempo limite de inferência excedido'
                    elif explicit_code:
                        err_code = explicit_code
                        message = raw_msg
                    elif 'Pesos ausentes' in raw_msg or 'não instalado' in raw_msg:
                        err_code = 'not_installed'
                        message = raw_msg
                    elif 'MODEL_RUNTIME_UNAVAILABLE' in raw_msg or 'indisponível' in raw_msg:
                        err_code = 'runtime_unavailable'
                        message = raw_msg
                    elif 'memória' in raw_msg or 'RAM' in raw_msg or 'OOM' in raw_msg:
                        err_code = 'oom'
                        message = raw_msg
                    else:
                        err_code = 'execution_error' if isinstance(exc, ValueError) else 'runtime_unavailable'
                        message = raw_msg if isinstance(exc, ValueError) else 'Runtime STT indisponível'
                    err_row = {
                        'model': selected,
                        'state': 'cancelled' if err_code == 'cancelled' else 'error',
                        'error_code': err_code,
                        'error': message,
                        'audio_sha256': digest,
                    }
                    if failure_detail is not None:
                        err_row['failure_detail'] = failure_detail
                    rows.append(err_row)
                    self.operations[(selected, variant)] = {'state': 'error', 'error_code': err_code, 'error': rows[-1]['error']}
                    if err_code in ('oom', 'timeout'):
                        self.validation_state[(selected, variant)] = {'load_status': err_code}
                    elif err_code not in ('not_installed', 'runtime_unavailable', 'cancelled', 'model_corrupted'):
                        self.validation_state[(selected, variant)] = {'load_status': 'model_load_failed'}
            return {'results': rows, 'sequential': True, 'dry_run': True, 'repetitions': repetitions,
                    'cancelled': self.cancel_requested,
                    'audio_decode_ms': decode_ms, 'request_total_ms': (time.perf_counter()-started)*1000,
                    'stop_to_transcript_ms': None, 'stop_to_transcript_note': 'Medido no navegador a partir do botão Parar; inclui espera, upload e rede.',
                    'audio_sha256': digest}

    async def apply(self, body):
        model, variant, _ = self.parameters(body)
        if body.get('confirm') is not True:
            raise ValueError('Confirme explicitamente a troca do modelo de produção')
        if self.busy.locked():
            raise ValueError('STT ocupado')
        invalidate_runtime_cache(model)
        async with self.busy:
            previous = copy.deepcopy(self.manager.config['stt'])
            backup = self.manager.data / 'stt-last-good.json'
            loaded_options = self.root.parent / 'options.json'
            active_before = await self.active()
            if active_before and backup.exists():
                bdata = json.loads(backup.read_text(encoding='utf-8'))
                if bdata.get('stt_model') == active_before.get('model'):
                    previous = bdata
            if active_before and loaded_options.exists():
                loaded = json.loads(loaded_options.read_text(encoding='utf-8'))
                if loaded.get('stt_model', 'fastconformer') == active_before.get('model') and (active_before.get('model') != 'fastconformer' or loaded.get('model_variant') == active_before.get('variant')):
                    previous = loaded
            previous['enabled'] = self.manager.config['stt']['enabled']
            from modules import validate_options
            proposed = validate_options('stt', {**self.manager.config['stt'], **body.get('options', {}), 'stt_model': model, 'model_variant': variant}, self.manager.data)
            restarted = False
            sig = inspect.signature(self.worker)
            supports_timeout = (
                any(p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD) for p in sig.parameters.values())
                or len(sig.parameters) >= 2
                or 'timeout' in sig.parameters
            )
            val_payload = {'command': 'validate', 'model': model, 'variant': variant, 'threads': proposed['threads']}
            val_timeout = self._model_timeout(model, 1)
            try:
                val_res = await (self.worker(val_payload, val_timeout) if supports_timeout else self.worker(val_payload))
                self.validation_state[(model, variant)] = {
                    'load_status': 'model_ready',
                    'load_ms': val_res.get('cold_load_ms') or val_res.get('model_load_ms'),
                    'process_rss_mb': val_res.get('process_rss_mb'),
                }
                await self.manager.configure('stt', {key: value for key, value in proposed.items() if key != 'enabled'})
                restarted = True
                await self.manager.stop('stt', persist=False)
                await self.manager.start('stt')
                deadline = time.monotonic() + max(90, val_timeout)
                while time.monotonic() < deadline:
                    active = await self.active()
                    if active and active.get('model') == model and (model != 'fastconformer' or active.get('variant') == variant):
                        from wyoming_gateway import describe_backend
                        capabilities = await describe_backend(10350)
                        compatible = any(any(item.get('name') == 'arandu-fastconformer-ptbr-int8' for item in program.get('models', []))
                                         for program in capabilities.get('asr', []))
                        if compatible:
                            staged = backup.with_suffix('.tmp')
                            staged.write_text(json.dumps(self.manager.config['stt']), encoding='utf-8')
                            staged.replace(backup)
                            invalidate_runtime_cache(model)
                            return {'applied': True, 'active': active}
                    process = self.manager.processes.get('stt')
                    if process and process.returncode is not None:
                        break
                    await asyncio.sleep(.5)
                raise ValueError('O novo modelo não confirmou prontidão Wyoming')
            except BaseException as exc:
                err_code = getattr(exc, 'error_code', None)
                if err_code in ('oom', 'timeout'):
                    self.validation_state[(model, variant)] = {'load_status': err_code}
                elif not isinstance(exc, asyncio.CancelledError):
                    self.validation_state[(model, variant)] = {'load_status': 'model_load_failed'}
                if restarted:
                    await self.manager.stop('stt', persist=False)
                self.manager.config['stt'] = previous
                self.manager.save()
                if restarted and previous['enabled']:
                    await self.manager.start('stt', persist=False)
                raise

    async def close(self):
        self._cancel_retention()
        await self._kill_session_proc()
        if self.install_task:
            self.install_task.cancel()
            await asyncio.gather(self.install_task, return_exceptions=True)
        for child in list(self.children):
            if child.returncode is None:
                child.kill()
            await child.wait()
