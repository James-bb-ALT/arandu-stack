#!/usr/bin/env python3
"""Arandu STT production: Wyoming + FastConformer CTC + lexical resolver.

Pipeline: Wyoming PCM -> optional synthetic tail padding -> FastConformer CTC ->
Arandu lexical resolver (full) -> Transcript. No Home Assistant commands are executed here.
"""
from __future__ import annotations

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
VERSION = '2.2.0'


def pcm16_to_float32(pcm: bytes) -> np.ndarray:
    if len(pcm) % 2:
        raise ValueError('PCM16 incompleto: numero impar de bytes')
    return np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768.0


def build_info() -> Info:
    attribution = Attribution(name='NVIDIA checkpoint + Arandu lexical resolver', url=UPSTREAM)
    return Info(asr=[AsrProgram(
        name='arandu-stt',
        description='FastConformer PT-BR CTC + Arandu lexical resolver',
        attribution=attribution,
        installed=True,
        version=VERSION,
        requires_external_vad=True,
        supports_transcript_streaming=False,
        models=[AsrModel(
            name=NAME,
            description='FastConformer PT-BR ONNX + contextual sanitation',
            attribution=attribution,
            installed=True,
            version=VERSION,
            languages=['pt-BR', 'pt'],
        )],
    )])


class Engine:
    def __init__(self, model_dir: Path, threads: int):
        import sherpa_onnx
        model = model_dir / 'model.onnx'
        if not model.is_file():
            model = model_dir / 'model.int8.onnx'
        tokens = model_dir / 'tokens.txt'
        if not model.is_file() or model.stat().st_size < 10_000_000 or not tokens.is_file():
            raise FileNotFoundError(f'Modelo ONNX incompleto em {model_dir}')
        self.recognizer = sherpa_onnx.OfflineRecognizer.from_nemo_ctc(
            model=str(model), tokens=str(tokens), num_threads=threads,
            sample_rate=16000, feature_dim=80, decoding_method='greedy_search',
            provider='cpu', debug=False,
        )
        self._lock = threading.Lock()

    def recognize(self, audio: np.ndarray) -> tuple[str, float, float]:
        before_wait = time.perf_counter()
        with self._lock:
            wait = time.perf_counter() - before_wait
            start = time.perf_counter()
            stream = self.recognizer.create_stream()
            stream.accept_waveform(16000, audio)
            self.recognizer.decode_stream(stream)
            return stream.result.text.strip(), time.perf_counter() - start, wait


class Metrics:
    def __init__(self, path: Path, debug_text: bool = False):
        self.path = path
        self.debug_text = debug_text
        self._lock = threading.Lock()
        self.latencies: list[float] = []

    def log(self, *, duration_s: float, inference_s: float, correction_ms: float,
            queue_s: float, after_stop_s: float, raw: str, final: str,
            changes: int, padding_ms: int, outcome: str, lexical: dict | None = None) -> None:
        entry = {
            'utc': datetime.now(timezone.utc).isoformat(),
            'model': NAME,
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
            with self.path.open('a', encoding='utf-8') as file:
                file.write(json.dumps(entry, ensure_ascii=False) + '\n')
            if outcome == 'ok':
                self.latencies.append(after_stop_s)
                self.latencies = self.latencies[-1000:]
                s = sorted(self.latencies)
                p95 = s[max(0, int((len(s) * .95 + .999999) - 1))]
                LOG.info('STT %.3fs | ASR %.3fs | correcao %.2fms | fila %.3fs | P95 %.3fs (n=%s)',
                         after_stop_s, inference_s, correction_ms, queue_s, p95, len(s))
                if self.debug_text:
                    LOG.info('RAW: %s', raw)
                    LOG.info('FINAL: %s', final)


class Handler(AsyncEventHandler):
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
        LOG.info('[WYOMING] client connected')

    def reset(self) -> None:
        self.converter = AudioChunkConverter(rate=16000, width=2, channels=1)
        self.parts.clear()
        self.total = 0
        self.invalid = False
        self.finished = False
        self.armed = True
        self.chunk_count = 0
        self.started = False

    async def handle_event(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            LOG.info('[WYOMING] Describe')
            await self.write_event(build_info().event())
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
                converted = self.converter.convert(chunk)
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
                    raw, infer, wait = await asyncio.to_thread(self.engine.recognize, audio)
                    resolved = await asyncio.to_thread(self.resolver.apply, raw, self.profile)
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
                             changes=changes, padding_ms=self.padding_ms, outcome=outcome, lexical=resolved)
            self.parts.clear()
            return False
        return True


def parse_args(argv=None):
    p = argparse.ArgumentParser(description='Arandu STT - Wyoming + FastConformer INT8 + lexical resolver')
    p.add_argument('--model-dir', type=Path, default=Path('/data/models/int8'))
    p.add_argument('--inventory', type=Path, default=Path('/data/arandu_ha_inventory.json'))
    p.add_argument('--uri', default='tcp://0.0.0.0:10350')
    p.add_argument('--threads', type=int, default=2)
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
    if not 1 <= a.threads <= 8:
        p.error('--threads precisa estar entre 1 e 8')
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
    LOG.info('Carregando FastConformer INT8 | threads=%s | profile=%s | padding=%sms', a.threads, a.profile, a.padding_ms)
    LOG.info('Resolver: %s', resolver.stats())
    engine = Engine(a.model_dir, a.threads)
    metrics = Metrics(a.metrics, a.debug_text)
    server = AsyncServer.from_uri(a.uri)
    LOG.info('PRONTO: Arandu STT Wyoming %s | profile=%s | padding=%sms', a.uri, a.profile, a.padding_ms)
    handler_factory = partial(Handler, engine, resolver, metrics, a.padding_ms, a.profile)
    refresh_task = asyncio.create_task(refresh_loop(resolver, a.inventory_refresh_seconds, query_ha=not a.no_ha_refresh)) if a.inventory_refresh_seconds else None
    try:
        await serve(server, handler_factory, a.no_discovery)
    finally:
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
