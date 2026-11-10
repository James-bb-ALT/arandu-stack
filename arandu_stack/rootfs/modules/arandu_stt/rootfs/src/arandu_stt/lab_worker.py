"""Bounded subprocess protocol for model management and acoustic experiments."""
from __future__ import annotations
import base64
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import sys
import time

from model_catalog import CATALOG, describe, install, location, verify


def run(body):
    root = Path(body['root'])
    model, variant = body.get('model', 'fastconformer'), body.get('variant', 'adapted')
    command = body['command']
    if command == 'models':
        return {'models': [describe(root, key, variant) for key in CATALOG]}
    if command == 'install':
        install(root, model, variant)
        return describe(root, model, variant)
    from backends import create_backend
    manifest = verify(root, model, variant)
    engine = create_backend(model, location(root, model, variant), body.get('threads', 2))
    try:
        engine.load()
        if command == 'validate':
            import numpy as np
            engine.recognize(np.zeros(16000, dtype=np.float32))
            return {'model': model, 'variant': variant, 'state': 'ready', 'model_load_ms': engine.load_ms}
        if command != 'test':
            raise ValueError('Comando STT inválido')
        import numpy as np
        from contextual_lexical import ContextualResolver
        from stt_processing import correct, evaluate
        pcm = base64.b64decode(body['pcm'], validate=True)
        if not 2 <= len(pcm) <= 384000 or len(pcm) % 2:
            raise ValueError('PCM inválido ou excedeu 12 segundos')
        audio = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768.0
        started = time.perf_counter()
        # Exact digital silence must never generate a Whisper hallucination.
        raw, inference_ms = ('', 0.0) if not np.any(audio) else engine.recognize(audio)
        cfg = body.get('options', {})
        here = Path(__file__).resolve().parent
        lexical_start = time.perf_counter()
        resolver = ContextualResolver(Path(body['inventory']), here/'resources', config_path=here/'config/lexical.json',
                    overrides={'enabled': cfg.get('lexical_enabled', True),
                               'closed_vocabulary': cfg.get('closed_vocabulary', True) if model == 'fastconformer' else False,
                               'source_weights': {'generic_lexicon': cfg.get('generic_weight', 7),
                                   'home_assistant': cfg.get('home_assistant_weight', 10)}})
        profile = body.get('profile') or (cfg.get('profile', 'full') if model == 'fastconformer' else 'normalize_only')
        resolved = correct(resolver, raw, profile)
        lexical_ms = (time.perf_counter() - lexical_start) * 1000
        import psutil
        return {'revision': manifest.get('revision') or manifest.get('version'), 'model_sha256': manifest.get('sha256'), 'model': model, 'variant': variant if model == 'fastconformer' else None, 'state': 'ready',
                'backend': CATALOG[model]['runtime'], 'device': 'cpu', 'compute': 'int8' if model != 'fastconformer' or variant == 'original' else 'fp32',
                'threads': body.get('threads', 2), 'profile': profile,
                'model_load_ms': engine.load_ms, 'inference_ms': inference_ms, 'lexical_ms': lexical_ms,
                'audio_duration_ms': len(pcm)/32, 'queue_ms': 0.0,
                'processing_ms': (time.perf_counter()-started)*1000,
                'realtime_factor': inference_ms / (len(pcm)/32),
                'process_rss_mb': psutil.Process(os.getpid()).memory_info().rss / 1048576,
                'raw_transcript': raw, 'final_transcript': resolved['text'],
                'safety_preserved_raw': resolved.get('safety_preserved_raw', False),
                'quality': evaluate(body.get('reference'), raw, resolved['text'], body.get('entity_reference')),
                'parameters': {'language': 'pt', 'beam_size': 1, 'vad_filter': False, 'condition_on_previous_text': False} if model != 'fastconformer' else {'decoding_method': 'greedy_search'}}
    finally:
        engine.close()


if __name__ == '__main__':
    try:
        body = json.loads(sys.stdin.buffer.read(2_097_153))
        with redirect_stdout(sys.stderr):
            result = run(body)
        print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    except Exception as exc:
        # No transcripts, paths, download tokens or upstream bodies in the protocol error.
        status = getattr(getattr(exc, 'response', None), 'status_code', None)
        message = ('Download recusado (HTTP ' + str(status) + '); tente novamente após verificar acesso à origem.') if type(status) is int else {
            'FileNotFoundError': 'Pesos ausentes; instale o modelo selecionado.',
            'ValueError': 'Modelo ou integridade inválidos; verifique os pesos e reinstale.',
            'ImportError': 'Dependência STT indisponível; atualize o app.',
            'ModuleNotFoundError': 'Dependência STT indisponível; atualize o app.',
            'ConnectionError': 'Falha de rede ao instalar pesos; tente novamente.',
        }.get(type(exc).__name__, 'Falha de carregamento, rede ou inferência; verifique o runtime e a origem dos pesos.')
        print(json.dumps({'error': type(exc).__name__, 'message': message}))
        sys.exit(1)
