#!/usr/bin/env python3
"""Arandu STT production: Wyoming + FastConformer/Whisper + lexical resolver.

Pipeline: Wyoming PCM -> optional synthetic tail padding -> FastConformer CTC ->
Arandu lexical resolver (full) -> Transcript. No Home Assistant commands are executed here.
"""
from __future__ import annotations

from model_catalog import CATALOG

import argparse
import asyncio
from contextlib import suppress
from datetime import datetime, timezone
from functools import partial
import json
import logging
from pathlib import Path
import threading
import time

import numpy as np
from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioChunkConverter, AudioStart, AudioStop
from wyoming.event import Event
from wyoming.error import Error
from wyoming.info import AsrModel, AsrProgram, Attribution, Describe, Info
from wyoming.server import AsyncEventHandler, AsyncServer

from contextual_lexical import ContextualResolver, LexicalError
from home_assistant_context import refresh_loop
from wyoming_discovery import DiscoveryError, publish_wyoming_discovery

HERE = Path(__file__).resolve().parent
LOG = logging.getLogger('arandu')
NAME = 'arandu-fastconformer-ptbr-int8'  # Preserve Assist's existing selected model ID.
UPSTREAM = 'https://huggingface.co/nvidia/stt_pt_fastconformer_hybrid_large_pc'
MAX_SECONDS = 35
MAX_INPUT_BYTES = 2 * 16000 * MAX_SECONDS
VERSION = '4.0.2'


def pcm16_to_float32(pcm: bytes) -> np.ndarray:
    if len(pcm) % 2:
        raise ValueError('PCM16 incompleto: numero impar de bytes')
    return np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768.0


def build_info(model='fastconformer') -> Info:
    from model_catalog import CATALOG
    selected = CATALOG[model]
    attribution = Attribution(name='NVIDIA checkpoint + Arandu lexical resolver', url=UPSTREAM)
    if model != 'fastconformer':
        attribution = Attribution(name=selected['name'] + ' (' + selected['license'] + ')', url='https://huggingface.co/' + selected['repo'])
    return Info(asr=[AsrProgram(
        name='arandu-stt',
        description=selected['name'] + ' PT-BR + Arandu lexical resolver',
        attribution=attribution,
        installed=True,
        version=VERSION,
        requires_external_vad=True,
        supports_transcript_streaming=False,
        models=[AsrModel(
            name=NAME,
            description=selected['name'] + ' + contextual sanitation (ID de serviço compatível)',
            attribution=attribution,
            installed=True,
            version=VERSION,
            languages=['pt-BR', 'pt'],
        )],
    )])


class Engine:
    def __init__(self, model_dir: Path, threads: int, model='fastconformer', variant='adapted'):
        from backends import create_backend
        self.model, self.variant, self.threads = model, variant, threads
        manifest_path = Path(model_dir) / 'manifest.json'
        self.manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {}
        self.backend = create_backend(model, model_dir, threads).load()
        import os
        import psutil
        if psutil.Process(os.getpid()).memory_info().rss > CATALOG[model]['memory_limit_mb'] * 1024**2:
            self.backend.close()
            raise MemoryError('STT excedeu o orçamento de RAM do modelo')
        self._lock = threading.Lock()
        self.clients = 0
        self.client_limit = 16
        self.recent = {}

    def recognize(self, audio):
        if not self._lock.acquire(blocking=False):
            raise RuntimeError('STT ocupado; tente novamente')
        try:
            if not np.any(audio):
                return '', 0.0, 0.0
            text, milliseconds = self.backend.recognize(audio)
            self.recent = {'inference_ms': milliseconds, 'audio_duration_ms': len(audio)/16}
            return text, milliseconds / 1000, 0.0
        finally:
            self._lock.release()

    def metadata(self):
        import os
        import psutil
        from model_catalog import CATALOG
        manifest = self.manifest
        return {'revision': manifest.get('revision') or manifest.get('version'), 'model_path': str(self.backend.model_dir), 'model': self.model, 'variant': self.variant, 'backend': CATALOG[self.model]['runtime'],
                'public_service_id': NAME, 'state': 'busy' if self._lock.locked() else 'ready', 'threads': self.threads, 'device': 'cpu',
                'compute': 'fp32' if self.model == 'fastconformer' and self.variant == 'adapted' else CATALOG[self.model]['compute'],
                'model_load_ms': self.backend.load_ms, 'process_rss_mb': psutil.Process(os.getpid()).memory_info().rss/1048576,
                'recent': self.recent, 'clients': self.clients, 'client_limit': self.client_limit}


class Metrics:
    def __init__(self, path: Path, debug_text: bool = False, model=NAME, variant=None):
        self.model, self.variant = model, variant
        self.path = path
        self.debug_text = debug_text
        self._lock = threading.Lock()
        self.latencies: list[float] = []

    def log(self, *, duration_s: float, inference_s: float, correction_ms: float,
            queue_s: float, after_stop_s: float, raw: str, final: str,
            changes: int, padding_ms: int, outcome: str, lexical: dict | None = None, audio_timings: dict | None = None) -> None:
        entry = {
            'utc': datetime.now(timezone.utc).isoformat(),
            'model': self.model,
            'model_variant': self.variant,
            'public_service_id': NAME,
            'duration_s': round(duration_s, 5),
            'padding_ms': padding_ms,
            'inference_s': round(inference_s, 5),
            'correction_ms': round(correction_ms, 3),
            'queue_s': round(queue_s, 5),
            'stop_to_transcript_s': round(after_stop_s, 5),
            'rtf_inference': round(inference_s / duration_s, 5) if duration_s else None,
            'changes': changes,
            'raw_chars': len(raw),
            'final_chars': len(final),
            'outcome': outcome,
            'audio_timings': audio_timings or {},
        }
        if self.debug_text:
            entry['raw'] = raw
            entry['final'] = final
        if lexical:
            entry['lexical'] = {k: lexical[k] for k in (
                'confidence', 'confidence_kind', 'confidence_is_calibrated_probability',
                'minimum_token_confidence', 'low_confidence', 'forced_corrections',
                'closed_vocabulary', 'all_tokens_allowed', 'context_generation', 'timings_ms') if k in lexical}
            if self.debug_text:
                entry['lexical']['tokens'] = lexical.get('tokens', [])
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size > 2*1024*1024:
                self.path.replace(self.path.with_suffix(self.path.suffix+'.1'))
            with self.path.open('a', encoding='utf-8') as file:
                file.write(json.dumps(entry, ensure_ascii=False) + '\n')
            if outcome == 'ok':
                self.latencies.append(after_stop_s)
                self.latencies = self.latencies[-1000:]
                s = sorted(self.latencies)
                p95 = ('%.3fs' % s[max(0, int((len(s) * .95 + .999999) - 1))]) if len(s) >= 20 else 'amostra insuficiente'
                LOG.info('STT %.3fs | ASR %.3fs | correcao %.2fms | fila %.3fs | P95 %s (n=%s)',
                         after_stop_s, inference_s, correction_ms, queue_s, p95, len(s))
                if self.debug_text:
                    LOG.info('RAW: %s', raw)
                    LOG.info('FINAL: %s', final)


class Handler(AsyncEventHandler):
    async def run(self):
        # Bound capture buffers and idle sockets even on the direct 10350 port.
        if self.engine.clients >= self.engine.client_limit:
            try:
                await self.write_event(Error(text='STT ocupado', code='queue_saturated').event())
            finally:
                await self.disconnect()
            return
        self.engine.clients += 1
        try:
            async with asyncio.timeout(120):
                await super().run()
        finally:
            self.engine.clients -= 1

    def __init__(self, engine: Engine, resolver: ContextualResolver, metrics: Metrics,
                 padding_ms: int, profile: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.engine = engine
        self.resolver = resolver
        self.metrics = metrics
        self.padding_ms = padding_ms
        self.profile = profile
        self.converter = AudioChunkConverter(rate=16000, width=2, channels=1)
        self.parts: list[bytes] = []
        self.total = 0
        self.invalid = False
        self.finished = False
        self.armed = False
        self.chunk_count = 0
        self.started = False
        self.capture_started = None
        self.conversion_ms = 0.0
        LOG.info('[WYOMING] client connected')

    async def disconnect(self):
        self.parts.clear()
        if self.writer is not None:
            self.writer.close()
            with suppress(OSError):
                await self.writer.wait_closed()

    def reset(self) -> None:
        self.converter = AudioChunkConverter(rate=16000, width=2, channels=1)
        self.parts.clear()
        self.total = 0
        self.invalid = False
        self.finished = False
        self.armed = True
        self.chunk_count = 0
        self.started = False
        self.capture_started = None
        self.conversion_ms = 0.0

    async def handle_event(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            LOG.info('[WYOMING] Describe')
            await self.write_event(build_info(getattr(self.engine, 'model', 'fastconformer')).event())
            return True
        if Transcribe.is_type(event.type):
            req = Transcribe.from_event(event)
            self.reset()
            requested = getattr(req, 'name', None)
            LOG.info('[WYOMING] Transcribe model=%s', requested or '<default>')
            if requested and requested != NAME:
                LOG.warning('Modelo pedido desconhecido: %s', requested)
                self.invalid = True
            return True
        if AudioStart.is_type(event.type):
            if not self.armed or self.finished:
                self.reset()
            start = AudioStart.from_event(event)
            self.started = True
            self.capture_started = time.perf_counter()
            LOG.info('[WYOMING] AudioStart rate=%s width=%s channels=%s',
                     start.rate, start.width, start.channels)
            return True
        if AudioChunk.is_type(event.type):
            if self.finished:
                return True
            if not self.armed:
                self.reset()
            if self.invalid:
                return True
            chunk = AudioChunk.from_event(event)
            try:
                if chunk.rate <= 0 or chunk.channels not in (1, 2) or chunk.width not in (1, 2, 3, 4):
                    raise ValueError('Formato de audio nao suportado')
                if chunk.rate > 96000 or chunk.rate < 8000 or len(chunk.audio) > 2_000_000:
                    raise ValueError('Chunk de audio fora dos limites')
                convert_started = time.perf_counter()
                converted = self.converter.convert(chunk)
                self.conversion_ms += (time.perf_counter()-convert_started)*1000
                if self.total + len(converted.audio) > MAX_INPUT_BYTES:
                    raise ValueError('Audio excedeu 35s')
                self.parts.append(converted.audio)
                self.total += len(converted.audio)
                self.chunk_count += 1
            except (ValueError, OverflowError, TypeError) as exc:
                LOG.warning('[WYOMING] Audio invalid: %s', exc)
                self.invalid = True
                self.parts.clear()
                self.total = 0
            return True
        if AudioStop.is_type(event.type):
            if self.finished:
                return False
            stop = time.perf_counter()
            self.finished = True
            pcm = b''.join(self.parts)
            duration = len(pcm) / 32000.0
            LOG.info('[WYOMING] AudioStop chunks=%s bytes=%s duration=%.3f',
                     self.chunk_count, len(pcm), duration)
            raw = final = ''
            infer = wait = correction_ms = 0.0
            changes = 0
            outcome = 'empty'
            resolved = {}
            error = None
            if self.invalid:
                outcome = 'invalid'
            elif duration >= .35:
                try:
                    audio = pcm16_to_float32(pcm)
                    if self.padding_ms:
                        audio = np.concatenate((audio, np.zeros(round(16000*self.padding_ms/1000), dtype=np.float32)))
                    raw, infer, wait = await asyncio.wait_for(asyncio.to_thread(self.engine.recognize, audio), 60)
                    from stt_processing import correct
                    resolved = await asyncio.to_thread(correct, self.resolver, raw, self.profile)
                    final = resolved['text']
                    correction_ms = float(resolved['correction_ms'])
                    changes = len(resolved['changes'])
                    outcome = 'ok'
                except LexicalError as exc:
                    outcome = 'failure'
                    error = Error(text=str(exc), code=exc.code)
                    LOG.error('Correção lexical indisponível: %s', exc.code)
                except Exception:
                    outcome = 'failure'
                    error = Error(text='Falha de inferência/correção', code='stt_failure')
                    LOG.exception('Erro de inferencia/correcao')
            context = {'arandu': resolved} if resolved.get('confidence_kind') == 'heuristic_lexical' else None
            if error:
                await self.write_event(error.event())
            else:
                await self.write_event(Transcript(text=final, language='pt-BR', context=context).event())
            LOG.info('[WYOMING] %s sent', 'Error' if error else 'Transcript')
            elapsed = time.perf_counter() - stop
            await asyncio.to_thread(self.metrics.log, duration_s=duration, inference_s=infer, correction_ms=correction_ms,
                             queue_s=wait, after_stop_s=elapsed, raw=raw, final=final,
                             changes=changes, padding_ms=self.padding_ms, outcome=outcome, lexical=resolved,
                             audio_timings={'capture_transport_ms':(stop-self.capture_started)*1000 if self.capture_started is not None else None,
                                 'resample_ms':self.conversion_ms,'buffer_bytes':len(pcm),'wake':'not_observed',
                                 'external_vad':'not_observed','assist_delivery':'not_observed'})
            self.parts.clear()
            return False
        return True


def parse_args(argv=None):
    p = argparse.ArgumentParser(description='Arandu STT multimodelo - Wyoming + lexical resolver')
    p.add_argument('--stt-model', choices=tuple(CATALOG), default='orukeet_int8')
    p.add_argument('--model-variant', choices=('adapted','original'), default='adapted')
    p.add_argument('--diagnostics-port', type=int, default=11560)
    p.add_argument('--model-dir', type=Path, default=Path('/data/models/orukeet_int8'))
    p.add_argument('--inventory', type=Path, default=Path('/data/arandu_ha_inventory.json'))
    p.add_argument('--uri', default='tcp://0.0.0.0:10350')
    p.add_argument('--threads', type=int, default=2)
    p.add_argument('--max-clients', type=int, default=16)
    p.add_argument('--padding-ms', type=int, choices=(0,100,200,300), default=0)
    p.add_argument('--profile', choices=('raw','normalize_only','acoustic_only','fuzzy_only','inventory_only','full','legacy_full'), default='full')
    p.add_argument('--lexical-config', type=Path, default=HERE/'config'/'lexical.json')
    p.add_argument('--lexical-disabled', action='store_true')
    p.add_argument('--open-vocabulary', action='store_true')
    p.add_argument('--inventory-refresh-seconds', type=int, default=60)
    p.add_argument('--no-ha-refresh', action='store_true')
    p.add_argument('--generic-weight', type=float)
    p.add_argument('--ha-weight', type=float)
    p.add_argument('--metrics', type=Path, default=Path('/data/metrics.jsonl'))
    p.add_argument('--debug-text', action='store_true')
    p.add_argument('--no-discovery', action='store_true')
    a = p.parse_args(argv)
    if not 1 <= a.threads <= 4:
        p.error('--threads precisa estar entre 1 e 4')
    if not 1 <= a.max_clients <= 32:
        p.error('--max-clients precisa estar entre 1 e 32')
    if not 0 <= a.inventory_refresh_seconds <= 86400:
        p.error('--inventory-refresh-seconds: 0–86400')
    return a


async def main(argv=None):
    a = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    overrides = {}
    if a.lexical_disabled:
        overrides['enabled'] = False
    if a.open_vocabulary:
        overrides['closed_vocabulary'] = False
    weights = {}
    if a.generic_weight is not None:
        weights.update({key: a.generic_weight for key in ('generic_lexicon','household','names','commands')})
    if a.ha_weight is not None:
        weights['home_assistant'] = a.ha_weight
    if weights:
        overrides['source_weights'] = weights
    resolver = ContextualResolver(a.inventory, HERE/'resources', config_path=a.lexical_config, overrides=overrides)
    LOG.info('Carregando %s | threads=%s | profile=%s | padding=%sms', a.stt_model, a.threads, a.profile, a.padding_ms)
    LOG.info('Resolver: %s', resolver.stats())
    engine = Engine(a.model_dir, a.threads, a.stt_model, a.model_variant)
    engine.client_limit = a.max_clients
    metrics = Metrics(a.metrics, a.debug_text, a.stt_model, a.model_variant)
    server = AsyncServer.from_uri(a.uri)
    LOG.info('PRONTO: Arandu STT Wyoming %s | profile=%s | padding=%sms', a.uri, a.profile, a.padding_ms)
    handler_factory = partial(Handler, engine, resolver, metrics, a.padding_ms, a.profile)
    refresh_task = asyncio.create_task(refresh_loop(resolver, a.inventory_refresh_seconds, query_ha=not a.no_ha_refresh)) if a.inventory_refresh_seconds else None
    from aiohttp import web
    diagnostics = web.Application()
    async def status(request):
        if request.remote not in {'127.0.0.1', '::1'} or request.headers.get('Origin'):
            raise web.HTTPForbidden()
        return web.json_response(engine.metadata())
    diagnostics.router.add_get('/status', status)
    runner = web.AppRunner(diagnostics, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, '127.0.0.1', a.diagnostics_port).start()
    try:
        await serve(server, handler_factory, a.no_discovery)
    finally:
        await runner.cleanup()
        engine.backend.close()
        if refresh_task:
            refresh_task.cancel()
            with suppress(asyncio.CancelledError):
                await refresh_task


async def serve(server, handler_factory, no_discovery):
    if hasattr(server, 'start'):
        await server.start(handler_factory)
        if not no_discovery:
            try:
                publish_wyoming_discovery(port=10350)
            except DiscoveryError as exc:
                LOG.warning('[DISCOVERY] %s', exc)
        await server._server.serve_forever()
        return
    if not no_discovery:
        try:
            publish_wyoming_discovery(port=10350)
        except DiscoveryError as exc:
            LOG.warning('[DISCOVERY] %s', exc)
    await server.run(handler_factory)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
