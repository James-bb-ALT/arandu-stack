"""Control-plane STT operations. Temporary engines never replace the Assist engine."""
from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
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
from model_catalog import CATALOG, describe, validate

MAX_UPLOAD = 1_400_000  # Keep the Base64 JSON envelope below the shared 2 MiB request budget.
MAX_PCM = 12 * 16000 * 2


async def watch_memory(process, limit):
    while process.returncode is None and getattr(process, 'pid', None):
        try:
            status = Path(f'/proc/{process.pid}/status').read_text()
            rss = next(int(line.split()[1]) for line in status.splitlines() if line.startswith('VmRSS:')) * 1024
            if rss > limit:
                process.kill()
                return
        except (OSError, StopIteration):
            pass
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
        self.install_task = None
        self.children = set()

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
        monitor = asyncio.create_task(watch_memory(process, CATALOG[body.get('model', 'fastconformer')]['memory_limit_mb'] * 1024**2))
        try:
            raw, _ = await asyncio.wait_for(process.communicate(json.dumps({**body, 'root': str(self.root)}).encode()), timeout)
            if len(raw) > 2_097_152:
                raise ValueError('Resposta STT excedeu o limite')
            result = json.loads(raw)
            if process.returncode or result.get('error'):
                raise ValueError(result.get('message', 'Falha no processo STT'))
            return result
        except BaseException:
            if process.returncode is None:
                process.kill()
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
        except (OSError, TimeoutError):
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
            if loading and row['id'] == self.manager.config['stt']['stt_model']:
                row['state'] = 'loading'
            if active is None and row['id'] == self.manager.config['stt']['stt_model'] and 'stt' in self.manager.failures:
                row.update(state='error', error='Falha no processo STT; consulte os logs e verifique os pesos.')
            key = (row['id'], variant)
            row.update(self.operations.get(key, {}))
            if row['state'] == 'downloading':
                progress = self.root / ('.install-' + row['id'] + '.json')
                try:
                    if json.loads(progress.read_text(encoding='utf-8')).get('state') == 'verifying':
                        row['state'] = 'verifying'
                except (OSError, ValueError):
                    pass
            if active and row['id'] == active.get('configured_model', active.get('model')) and (row['id'] != 'fastconformer' or variant == active.get('variant')):
                if active.get('model_loaded') is True:
                    row.update(state='ready', ready=True, active=True)
                else:
                    row.update(state=active.get('state', 'waiting_first_use'), ready=False, active=False)
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
        key = (model, variant)
        (self.root / ('.install-' + model + '.json')).unlink(missing_ok=True)
        self.operations[key] = {'state': 'downloading'}
        async def install_one():
            async with self.busy:
                try:
                    await self.worker({'command': 'install', 'model': model, 'variant': variant}, 900)
                    self.operations[key] = {'state': 'installed'}
                except Exception as exc:
                    detail = str(exc) if isinstance(exc, ValueError) else ('Tempo limite excedido' if isinstance(exc, TimeoutError) else 'Runtime STT ou rede indisponível')
                    self.operations[key] = {'state': 'error', 'error': detail + ' Cache anterior preservado; tente novamente.'}
        self.install_task = asyncio.create_task(install_one())
        return {'state': 'downloading', 'model': model}

    async def remove(self, model, variant='adapted'):
        validate(model, variant)
        if self.busy.locked():
            raise ValueError('STT ocupado; aguarde a operação atual')
        active = await self.active()
        if active and active.get('active_model') == model and active.get('model_loaded'):
            raise ValueError('Não é possível remover o modelo ativo em RAM; troque ou desative antes')
        async with self.busy:
            result = await self.worker({'command': 'remove', 'model': model, 'variant': variant}, 60)
            self.operations.pop((model, variant), None)
            return result

    def parameters(self, body):
        model, variant = body.get('model', self.manager.config['stt']['stt_model']), body.get('variant', 'adapted')
        validate(model, variant)
        threads = body.get('threads', self.manager.config['stt']['threads'])
        profile = body.get('profile')
        if type(threads) is not int or not 1 <= threads <= 4 or profile not in (None, 'raw', 'normalize_only', 'full'):
            raise ValueError('Threads ou perfil STT inválidos')
        return model, variant, threads

    async def test(self, body, compare=False):
        model, variant, threads = self.parameters(body)
        models = body.get('models', [model]) if compare else [model]
        if type(models) is not list or not 1 <= len(models) <= 12 or any(type(item) is not str for item in models) or len(set(models)) != len(models):
            raise ValueError('Selecione de 1 a 12 modelos distintos')
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
        async with self.busy:
            started = time.perf_counter()
            pcm, decode_ms = await decode_audio(base64.b64decode(encoded, validate=True))
            rows = []
            digest = hashlib.sha256(pcm).hexdigest()
            for selected in models:
                item_started = time.perf_counter()
                self.operations[(selected, variant)] = {'state': 'loading'}
                try:
                    row = await self.worker({'command': 'test', 'model': selected, 'variant': variant,
                        'threads': threads, 'profile': body.get('profile'), 'reference': reference, 'entity_reference': entity_reference,
                        'preset': body.get('preset', 'original'),
                        'audio_processing': body.get('audio_processing') if isinstance(body.get('audio_processing'), dict) else None,
                        'nemotron_streaming_profile': body.get('nemotron_streaming_profile'),
                        'options': self.manager.config['stt'], 'pcm': base64.b64encode(pcm).decode(),
                        'inventory': str(self.root.parent/'arandu_ha_inventory.json')})
                    row.update(audio_decode_ms=decode_ms, audio_sha256=digest,
                               request_total_ms=(time.perf_counter()-item_started)*1000)
                    rows.append(row)
                    self.operations.pop((selected, variant), None)
                except (ValueError, TimeoutError, OSError) as exc:
                    message = str(exc) if isinstance(exc, ValueError) else ('Tempo limite de inferência excedido' if isinstance(exc, TimeoutError) else 'Runtime STT indisponível')
                    rows.append({'model': selected, 'state': 'error', 'error': message, 'audio_sha256': digest})
                    self.operations[(selected, variant)] = {'state': 'error', 'error': rows[-1]['error']}
            return {'results': rows, 'sequential': True, 'dry_run': True,
                    'audio_decode_ms': decode_ms, 'request_total_ms': (time.perf_counter()-started)*1000,
                    'stop_to_transcript_ms': None, 'stop_to_transcript_note': 'Medido no navegador a partir do botão Parar; inclui espera, upload e rede.',
                    'audio_sha256': digest}

    async def apply(self, body):
        model, variant, _ = self.parameters(body)
        if body.get('confirm') is not True:
            raise ValueError('Confirme explicitamente a troca do modelo de produção')
        if self.busy.locked():
            raise ValueError('STT ocupado')
        async with self.busy:
            previous = copy.deepcopy(self.manager.config['stt'])
            backup = self.manager.data / 'stt-last-good.json'
            if backup.exists():
                previous = json.loads(backup.read_text(encoding='utf-8'))
            loaded_options = self.root.parent / 'options.json'
            active_before = await self.active()
            if active_before and loaded_options.exists():
                loaded = json.loads(loaded_options.read_text(encoding='utf-8'))
                if loaded.get('stt_model', 'fastconformer') == active_before.get('model') and loaded.get('model_variant') == active_before.get('variant'):
                    previous = loaded
            previous['enabled'] = self.manager.config['stt']['enabled']
            from modules import validate_options
            proposed = validate_options('stt', {**self.manager.config['stt'], **body.get('options', {}), 'stt_model': model, 'model_variant': variant}, self.manager.data)
            restarted = False
            try:
                await self.worker({'command': 'validate', 'model': model, 'variant': variant, 'threads': proposed['threads']})
                await self.manager.configure('stt', {key: value for key, value in proposed.items() if key != 'enabled'})
                restarted = True
                await self.manager.stop('stt', persist=False)
                await self.manager.start('stt')
                deadline = time.monotonic() + 90
                while time.monotonic() < deadline:
                    active = await self.active()
                    if active and active.get('model') == model and active.get('variant') == variant:
                        from wyoming_gateway import describe_backend
                        capabilities = await describe_backend(10350)
                        compatible = any(any(item.get('name') == 'arandu-fastconformer-ptbr-int8' for item in program.get('models', []))
                                         for program in capabilities.get('asr', []))
                        if compatible:
                            staged = backup.with_suffix('.tmp')
                            staged.write_text(json.dumps(self.manager.config['stt']), encoding='utf-8')
                            staged.replace(backup)
                            return {'applied': True, 'active': active}
                    process = self.manager.processes.get('stt')
                    if process and process.returncode is not None:
                        break
                    await asyncio.sleep(.5)
                raise ValueError('O novo modelo não confirmou prontidão Wyoming')
            except BaseException:
                if restarted:
                    await self.manager.stop('stt', persist=False)
                self.manager.config['stt'] = previous
                self.manager.save()
                if restarted and previous['enabled']:
                    await self.manager.start('stt', persist=False)
                raise

    async def close(self):
        if self.install_task:
            self.install_task.cancel()
            await asyncio.gather(self.install_task, return_exceptions=True)
        for child in list(self.children):
            if child.returncode is None:
                child.kill()
            await child.wait()
