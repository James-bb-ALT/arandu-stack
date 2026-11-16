"""Bounded subprocess protocol for model management and acoustic experiments."""
from __future__ import annotations
import base64
from contextlib import redirect_stdout
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time

from model_catalog import CATALOG, describe, install, location, remove, verify


def _process_tree_rss_mb() -> float:
    import psutil
    proc = psutil.Process(os.getpid())
    total = proc.memory_info().rss
    children_fn = getattr(proc, 'children', None)
    if callable(children_fn):
        for child in children_fn(recursive=True) or ():
            try:
                total += child.memory_info().rss
            except (psutil.Error, AttributeError):
                pass
    return total / 1048576


def _safe_engine_rss_mb(engine) -> float:
    fn = getattr(engine, 'process_rss_mb', None)
    if callable(fn):
        try:
            val = fn()
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                return float(val)
        except Exception:
            pass
    return 0.0


def _safe_engine_str(engine, attr: str, default: str | None = None) -> str | None:
    val = getattr(engine, attr, default)
    return val if isinstance(val, str) else default


def _encode_wav_b64(audio):
    import io
    import wave
    import numpy as np
    pcm = np.clip(np.round(np.asarray(audio, dtype=np.float32) * 32768.0), -32768, 32767).astype('<i2')
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(pcm.tobytes())
    return base64.b64encode(buf.getvalue()).decode('ascii')


def run(body):
    root = Path(body['root'])
    model, variant = body.get('model', 'fastconformer'), body.get('variant', 'adapted')
    command = body['command']
    if command == 'models':
        return {'models': [describe(root, key, variant) for key in CATALOG]}
    if command == 'remove':
        return remove(root, model, variant)
    if command == 'install':
        from backends import create_backend
        def validate_load(directory):
            engine = create_backend(model, directory, body.get('threads', 2))
            try:
                engine.load()
            finally:
                engine.close()
        def progress(state, **extra):
            status = root / ('.install-' + model + '.json')
            staged = status.with_suffix('.tmp')
            payload = {'state': state, **extra}
            staged.write_text(json.dumps(payload), encoding='utf-8')
            staged.replace(status)
        install(root, model, variant, progress=progress, validator=validate_load)
        return describe(root, model, variant)
    if command not in ('validate', 'test'):
        raise ValueError('Comando STT inválido')
    if command == 'test':
        if body.get('language') not in (None, 'pt') or body.get('translate') is True or body.get('detect_language') is True:
            raise ValueError('Idioma fixo em português (pt); tradução e detecção automática não são permitidas')
        raw_reps = body.get('repetitions', 1)
        if raw_reps is not None and (isinstance(raw_reps, bool) or not isinstance(raw_reps, int) or raw_reps not in (1, 3, 5, 10)):
            raise ValueError('Repetições inválidas; escolha 1, 3, 5 ou 10')
    from backends import create_backend
    manifest = verify(root, model, variant)
    engine = create_backend(model, location(root, model, variant), body.get('threads', 2))
    if model == 'nemotron_unified_0_6b':
        req_prof = body.get('nemotron_streaming_profile') or body.get('nemotron_profile') or 'offline'
        if req_prof:
            engine.streaming_profile = req_prof
    try:
        engine.load()
        if command == 'validate':
            import numpy as np
            engine.recognize(np.zeros(16000, dtype=np.float32))
            rss_mb = round(max(_process_tree_rss_mb(), _safe_engine_rss_mb(engine)), 2)
            return {
                'model': model,
                'variant': variant,
                'state': 'ready',
                'load_status': 'model_ready',
                'model_load_ms': engine.load_ms,
                'cold_load_ms': engine.load_ms,
                'process_rss_mb': rss_mb,
                'inference_mode': _safe_engine_str(engine, 'last_inference_mode', 'offline') or 'offline',
            }
        import numpy as np
        from audio_processor import AudioProcessor, AudioProcessorConfig, PRESETS
        from contextual_lexical import ContextualResolver
        from stt_processing import correct, evaluate
        from vad_processor import SileroVADProcessor, VadConfig, ensure_silero_vad
        pcm = base64.b64decode(body['pcm'], validate=True)
        if not 2 <= len(pcm) <= 384000 or len(pcm) % 2:
            raise ValueError('PCM inválido ou excedeu 12 segundos')
        cfg = body.get('options', {})
        preset_name = body.get('preset', 'original')
        ap_cfg = dict(PRESETS.get(preset_name, {}))
        if preset_name == 'custom' and isinstance(body.get('audio_processing'), dict):
            ap_cfg.update(body['audio_processing'])
        elif isinstance(body.get('audio_processing'), dict) and not preset_name:
            ap_cfg.update(body['audio_processing'])

        started = time.perf_counter()
        processor = AudioProcessor(AudioProcessorConfig(
            sample_rate=16000,
            num_channels=1,
            high_pass_filter=bool(ap_cfg.get('high_pass_filter', False)),
            noise_suppression=bool(ap_cfg.get('noise_suppression', False)),
            ns_level=int(ap_cfg.get('ns_level', 1)),
            auto_gain_control=bool(ap_cfg.get('auto_gain_control', False)),
            agc_gain_db=float(ap_cfg.get('agc_gain_db', 0.0)),
            echo_cancellation=bool(ap_cfg.get('echo_cancellation', False)),
        ))
        try:
            audio, ap_stats = processor.process_pcm16(pcm, sample_rate=16000, channels=1)
            tail = processor.flush()
            if len(tail):
                audio = np.concatenate((audio, tail))
        finally:
            processor.close()

        vad_stats = {'vad_enabled': bool(ap_cfg.get('vad_enabled', False)), 'vad_ms': 0.0, 'speech_detected': True, 'end_of_speech_ms': None}
        if ap_cfg.get('vad_enabled', False) and len(audio):
            vad_path = ensure_silero_vad(root / '_vad', allow_download=True)
            vad = SileroVADProcessor(vad_path, VadConfig(
                threshold=float(ap_cfg.get('vad_threshold', 0.50)),
                min_speech_ms=int(ap_cfg.get('vad_min_speech_ms', 180)),
                min_silence_ms=int(ap_cfg.get('vad_min_silence_ms', 450)),
                pre_roll_ms=int(ap_cfg.get('vad_pre_roll_ms', 300)),
                post_roll_ms=int(ap_cfg.get('vad_post_roll_ms', 200)),
            ))
            try:
                vres = vad.process_audio(audio)
                vad_stats.update(
                    vad_ms=vres.vad_ms,
                    speech_detected=vres.speech_detected,
                    end_of_speech_ms=vres.end_of_speech_ms,
                    speech_ms=vres.speech_ms,
                )
                audio = vres.audio
            finally:
                vad.close()

        raw_reps = body.get('repetitions', 1)
        repetitions = 1 if raw_reps is None else int(raw_reps)
        do_warmup = bool(body.get('warmup', repetitions > 1))
        # Exact digital silence or VAD-filtered silence must never generate a hallucination.
        if not len(audio) or not np.any(audio):
            raw = ''
            warmup_ms = 0.0
            samples = [0.0] * repetitions
        else:
            warmup_ms = 0.0
            if do_warmup:
                _, warmup_ms = engine.recognize(audio)
            samples = []
            raw = ''
            for idx in range(repetitions):
                transcript, elapsed_ms = engine.recognize(audio)
                if idx == 0:
                    raw = transcript
                samples.append(float(elapsed_ms))
        ordered = sorted(samples)
        n = len(ordered)
        p50 = float(statistics.median(ordered))
        p95_idx = min(n - 1, max(0, int(math.ceil(n * 0.95)) - 1))
        inference_stats = {
            'repetitions': n,
            'warmup_executed': bool(do_warmup and len(audio) and np.any(audio)),
            'warmup_ms': round(warmup_ms, 3),
            'samples_ms': [round(x, 3) for x in samples],
            'min_ms': round(ordered[0], 3),
            'mean_ms': round(float(statistics.fmean(ordered)), 3),
            'median_ms': round(p50, 3),
            'p50_ms': round(p50, 3),
            'p95_ms': round(ordered[p95_idx], 3),
            'max_ms': round(ordered[-1], 3),
            'stdev_ms': round(float(statistics.pstdev(ordered)) if n > 1 else 0.0, 3),
        }
        inference_ms = p50
        here = Path(__file__).resolve().parent
        lexical_start = time.perf_counter()
        resolver = ContextualResolver(Path(body['inventory']), here/'resources', config_path=here/'config/lexical.json',
                    overrides={'enabled': cfg.get('lexical_enabled', True),
                               'closed_vocabulary': cfg.get('closed_vocabulary', True) if model == 'fastconformer' else False,
                               'source_weights': {'generic_lexicon': cfg.get('generic_weight', 7),
                                   'home_assistant': cfg.get('home_assistant_weight', 10)}})
        profile = body.get('profile') or (cfg.get('profile', 'full') if model == 'fastconformer' else cfg.get('whisper_profile', 'normalize_only'))
        resolved = correct(resolver, raw, profile)
        lexical_ms = (time.perf_counter() - lexical_start) * 1000
        if model in ('whisper_cpp_small', 'whisper_cpp_medium'):
            params = {
                'language': 'pt',
                'language_fixed': True,
                'translate': False,
                'detect_language': False,
                'beam_size': 1,
                'best_of': 1,
                'temperature': 0.0,
                'max_context': 0,
                'persistent_session': True,
            }
        elif model.startswith('whisper_'):
            params = {'language': 'pt', 'beam_size': 1, 'vad_filter': False, 'condition_on_previous_text': False}
        elif model in ('granite_turboctc_q4', 'parakeet_ultra_q8', 'parakeet_ultra_q4'):
            params = {'language': 'pt', 'timestamps': 'none', 'persistent_session': True}
        elif model == 'nemotron_unified_0_6b':
            params = {
                'language': 'pt',
                'language_fixed': True,
                'translate': False,
                'detect_language': False,
                'streaming_profile': _safe_engine_str(engine, 'streaming_profile', 'offline') or 'offline',
                'inference_mode': _safe_engine_str(engine, 'last_inference_mode', 'offline') or 'offline',
                'persistent_worker': True,
            }
        else:
            params = {'decoding_method': 'greedy_search'}
        rss_mb = round(max(_process_tree_rss_mb(), _safe_engine_rss_mb(engine)), 2)
        total_ms = round(float(engine.load_ms or 0.0) + float(inference_ms or 0.0), 3)
        return {'revision': manifest.get('revision') or manifest.get('version'), 'model_sha256': manifest.get('sha256'), 'model': model, 'variant': variant if model == 'fastconformer' else None, 'state': 'ready',
                'backend': CATALOG[model]['runtime'], 'license': CATALOG[model]['license'], 'device': 'cpu', 'compute': 'fp32' if model == 'fastconformer' and variant == 'adapted' else CATALOG[model]['compute'],
                'language': 'pt', 'language_fixed': True, 'language_label': CATALOG[model].get('language_label', 'Português — fixo'),
                'threads': body.get('threads', 2), 'profile': profile, 'preset': preset_name,
                'inference_mode': _safe_engine_str(engine, 'last_inference_mode', 'offline') or 'offline',
                'streaming_note': _safe_engine_str(engine, 'last_streaming_note', None),
                'model_load_ms': engine.load_ms, 'cold_load_ms': engine.load_ms,
                'warmup_ms': inference_stats['warmup_ms'], 'repetitions': n,
                'inference_ms': inference_ms, 'total_ms': total_ms, 'inference_stats': inference_stats, 'lexical_ms': lexical_ms,
                'preprocess_ms': ap_stats['preprocess_ms'], 'vad_ms': vad_stats['vad_ms'],
                'end_of_speech_ms': vad_stats['end_of_speech_ms'], 'speech_detected': vad_stats['speech_detected'],
                'aec': ap_stats['aec'], 'processed_wav_b64': _encode_wav_b64(audio) if len(audio) else None,
                'audio_duration_ms': len(pcm)/32, 'queue_ms': 0.0,
                'processing_ms': (time.perf_counter()-started)*1000,
                'realtime_factor': inference_ms / (len(pcm)/32),
                'estimated_ram_mb': CATALOG[model].get('estimated_ram_mb'),
                'process_rss_mb': rss_mb,
                'raw_transcript': raw, 'final_transcript': resolved['text'],
                'safety_preserved_raw': resolved.get('safety_preserved_raw', False),
                'quality': evaluate(body.get('reference'), raw, resolved['text'], body.get('entity_reference')),
                'parameters': params}
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
        structured_code = getattr(exc, 'code', None)
        if type(status) is int:
            error_code = 'download_http_error'
            message = 'Download recusado (HTTP ' + str(status) + '); tente novamente após verificar acesso à origem.'
        elif isinstance(exc, MemoryError):
            error_code = 'oom'
            message = 'Memória insuficiente para carregar ou executar o modelo selecionado (OOM).'
        elif isinstance(exc, TimeoutError):
            error_code = 'timeout'
            message = 'Tempo limite excedido durante carga ou inferência do modelo.'
        elif structured_code:
            error_code = 'runtime_unavailable' if structured_code == 'MODEL_RUNTIME_UNAVAILABLE' else 'execution_error'
            message = str(exc)[:240]
        elif isinstance(exc, FileNotFoundError):
            error_code = 'not_installed'
            message = 'Pesos ausentes; instale o modelo selecionado.'
        elif isinstance(exc, ValueError) and any(
            tok in str(exc) for tok in (
                'Tamanho do arquivo inválido',
                'Integridade do modelo inválida',
                'Hash difere da origem pinada',
                'SHA256',
                'Revisão instalada difere',
                'Manifesto Whisper',
                'Modelo legado incompleto',
                'corrompido',
                'truncado',
                'Git LFS',
            )
        ):
            error_code = 'model_corrupted'
            message = str(exc)
        elif isinstance(exc, ValueError) and str(exc).startswith((
            'Espaço insuficiente para download',
            'Idioma fixo em português',
            'Repetições inválidas',
            'Tradução automática não é permitida',
            'Detecção automática de idioma não é permitida',
            'Arquivo de pesos whisper.cpp',
        )):
            error_code = 'validation_error'
            message = str(exc)
        else:
            error_code = 'execution_error'
            message = {
                'ValueError': 'Modelo ou integridade inválidos; verifique os pesos e reinstale.',
                'ImportError': 'Dependência STT indisponível; atualize o app.',
                'ModuleNotFoundError': 'Dependência STT indisponível; atualize o app.',
                'ConnectionError': 'Falha de rede ao instalar pesos; tente novamente.',
            }.get(type(exc).__name__, 'Falha de carregamento, rede ou inferência; verifique o runtime e a origem dos pesos.')
        print(json.dumps({'error': type(exc).__name__, 'error_code': error_code, 'message': message}))
        sys.exit(1)
