"""Bounded subprocess protocol (one-shot and persistent `--serve`) for STT lab & benchmarks."""
from __future__ import annotations

import base64
from contextlib import redirect_stdout
import gc
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
from typing import Any

from memory_budget import classify_memory_failure, compute_memory_budget
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


def _process_tree_cpu_seconds() -> float:
    import psutil

    proc = psutil.Process(os.getpid())
    cpu_fn = getattr(proc, 'cpu_times', None)
    if not callable(cpu_fn):
        return 0.0
    try:
        ct = cpu_fn()
        total = float(ct.user + ct.system)
    except (psutil.Error, AttributeError):
        return 0.0
    children_fn = getattr(proc, 'children', None)
    if callable(children_fn):
        for child in children_fn(recursive=True) or ():
            try:
                cct = child.cpu_times()
                total += float(cct.user + cct.system)
            except (psutil.Error, AttributeError):
                pass
    return total


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


def _safe_engine_dict(engine, attr: str) -> dict[str, Any] | None:
    val = getattr(engine, attr, None)
    return dict(val) if isinstance(val, dict) else None


def _is_engine_alive(engine) -> bool:
    if engine is None:
        return False
    proc = getattr(engine, '_proc', None)
    if proc is not None and hasattr(proc, 'poll'):
        return proc.poll() is None
    if hasattr(engine, '_model'):
        return getattr(engine, '_model', None) is not None
    return True


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


def _validate_test_params(body: dict[str, Any]) -> None:
    if body.get('language') not in (None, 'pt') or body.get('translate') is True or body.get('detect_language') is True:
        raise ValueError('Idioma fixo em português (pt); tradução e detecção automática não são permitidas')
    raw_reps = body.get('repetitions', 1)
    if raw_reps is not None and (isinstance(raw_reps, bool) or not isinstance(raw_reps, int) or raw_reps not in (1, 3, 5, 10)):
        raise ValueError('Repetições inválidas; escolha 1, 3, 5 ou 10')


def _execute_test_on_engine(
    body: dict[str, Any],
    *,
    engine: Any,
    manifest: dict[str, Any],
    root: Path,
    cold_load_ms: float,
    session_reused: bool,
    memory_budget: dict[str, Any] | None = None,
) -> dict[str, Any]:
    import numpy as np
    from audio_processor import AudioProcessor, AudioProcessorConfig, PRESETS
    from contextual_lexical import ContextualResolver
    from stt_processing import correct, evaluate
    from vad_processor import SileroVADProcessor, VadConfig, ensure_silero_vad

    model = body.get('model', 'fastconformer')
    variant = body.get('variant', 'adapted')
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
    ram_initial_mb = round(max(_process_tree_rss_mb(), _safe_engine_rss_mb(engine)), 2)

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
    exec_mode = str(body.get('execution_mode') or ('warm' if session_reused else 'cold'))
    do_warmup = bool(body.get('warmup', repetitions > 1 or exec_mode == 'warmup_benchmark'))

    ram_peak_mb = ram_initial_mb
    cpu_before = _process_tree_cpu_seconds()
    infer_wall_start = time.perf_counter()

    if not len(audio) or not np.any(audio):
        raw = ''
        warmup_ms = 0.0
        samples = [0.0] * repetitions
    else:
        warmup_ms = 0.0
        if do_warmup:
            _, warmup_ms = engine.recognize(audio)
            ram_peak_mb = max(ram_peak_mb, round(max(_process_tree_rss_mb(), _safe_engine_rss_mb(engine)), 2))
        samples = []
        raw = ''
        for idx in range(repetitions):
            transcript, elapsed_ms = engine.recognize(audio)
            if idx == 0:
                raw = transcript
            samples.append(float(elapsed_ms))
            ram_peak_mb = max(ram_peak_mb, round(max(_process_tree_rss_mb(), _safe_engine_rss_mb(engine)), 2))

    infer_wall_s = max(time.perf_counter() - infer_wall_start, 1e-6)
    cpu_delta_ms = max(0.0, (_process_tree_cpu_seconds() - cpu_before) * 1000.0)
    cpu_percent = round(min(400.0, (cpu_delta_ms / 1000.0) / infer_wall_s * 100.0), 1)

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
    resolver = ContextualResolver(
        Path(body['inventory']),
        here / 'resources',
        config_path=here / 'config/lexical.json',
        overrides={
            'enabled': cfg.get('lexical_enabled', True),
            'closed_vocabulary': cfg.get('closed_vocabulary', True) if model == 'fastconformer' else False,
            'source_weights': {
                'generic_lexicon': cfg.get('generic_weight', 7),
                'home_assistant': cfg.get('home_assistant_weight', 10),
            },
        },
    )
    profile = body.get('profile') or (cfg.get('profile', 'full') if model == 'fastconformer' else cfg.get('whisper_profile', 'normalize_only'))
    resolved = correct(resolver, raw, profile)
    lexical_ms = (time.perf_counter() - lexical_start) * 1000

    if model.startswith('whisper_cpp_'):
        params = {
            'language': 'pt',
            'language_fixed': True,
            'translate': False,
            'detect_language': False,
            'beam_size': 1,
            'best_of': 1,
            'temperature': 0.0,
            'max_context': 0,
            'quantization': CATALOG[model].get('quantization', 'fp16'),
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

    ram_final_mb = round(max(_process_tree_rss_mb(), _safe_engine_rss_mb(engine)), 2)
    ram_peak_mb = max(ram_peak_mb, ram_final_mb)

    effective_audio_ms = round((len(audio) / 16.0) if len(audio) else 0.0, 2)
    input_audio_ms = len(pcm) / 32.0
    # Warm benchmarks strictly exclude cold load latency.
    reported_cold_ms = 0.0 if session_reused else round(float(cold_load_ms or 0.0), 3)
    total_ms = round(reported_cold_ms + float(inference_ms or 0.0), 3)

    return {
        'revision': manifest.get('revision') or manifest.get('version'),
        'model_sha256': manifest.get('sha256'),
        'model': model,
        'variant': variant if model == 'fastconformer' else None,
        'quantization': CATALOG[model].get('quantization'),
        'state': 'ready',
        'backend': CATALOG[model]['runtime'],
        'license': CATALOG[model]['license'],
        'device': 'cpu',
        'compute': 'fp32' if model == 'fastconformer' and variant == 'adapted' else CATALOG[model]['compute'],
        'language': 'pt',
        'language_fixed': True,
        'language_label': CATALOG[model].get('language_label', 'Português — fixo'),
        'threads': body.get('threads', 2),
        'profile': profile,
        'preset': preset_name,
        'execution_mode': exec_mode,
        'session_reused': session_reused,
        'inference_mode': _safe_engine_str(engine, 'last_inference_mode', 'offline') or 'offline',
        'streaming_note': _safe_engine_str(engine, 'last_streaming_note', None),
        'model_load_ms': reported_cold_ms,
        'cold_load_ms': reported_cold_ms,
        'warmup_ms': inference_stats['warmup_ms'],
        'repetitions': n,
        'inference_ms': inference_ms,
        'total_ms': total_ms,
        'inference_stats': inference_stats,
        'backend_timings': _safe_engine_dict(engine, 'last_timings'),
        'lexical_ms': lexical_ms,
        'preprocess_ms': ap_stats['preprocess_ms'],
        'vad_ms': vad_stats['vad_ms'],
        'end_of_speech_ms': vad_stats['end_of_speech_ms'],
        'speech_detected': vad_stats['speech_detected'],
        'aec': ap_stats['aec'],
        'processed_wav_b64': _encode_wav_b64(audio) if len(audio) else None,
        'audio_duration_ms': input_audio_ms,
        'effective_audio_duration_ms': effective_audio_ms,
        'queue_ms': 0.0,
        'processing_ms': (time.perf_counter() - started) * 1000,
        'realtime_factor': inference_ms / max(effective_audio_ms or input_audio_ms, 1e-3),
        'estimated_ram_mb': CATALOG[model].get('estimated_ram_mb'),
        'process_rss_mb': ram_final_mb,
        'ram_initial_mb': ram_initial_mb,
        'ram_peak_mb': ram_peak_mb,
        'ram_final_mb': ram_final_mb,
        'cpu_time_ms': round(cpu_delta_ms, 2),
        'cpu_percent': cpu_percent,
        'memory_budget': memory_budget,
        'raw_transcript': raw,
        'final_transcript': resolved['text'],
        'safety_preserved_raw': resolved.get('safety_preserved_raw', False),
        'quality': evaluate(body.get('reference'), raw, resolved['text'], body.get('entity_reference')),
        'parameters': params,
    }


def _create_configured_backend(model: str, directory: Path, threads: int, memory_limit_mb: int | None, body: dict[str, Any]):
    from backends import create_backend

    if model == 'nemotron_unified_0_6b':
        engine = create_backend(model, directory, threads, memory_limit_mb=memory_limit_mb)
        req_prof = body.get('nemotron_streaming_profile') or body.get('nemotron_profile') or 'offline'
        if req_prof:
            engine.streaming_profile = req_prof
        return engine
    return create_backend(model, directory, threads)


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
        _validate_test_params(body)

    manifest = verify(root, model, variant)
    budget = compute_memory_budget(
        policy=body.get('memory_policy', 'auto'),
        custom_limit_mb=body.get('memory_limit_mb'),
        catalog_limit_mb=CATALOG[model].get('memory_limit_mb'),
        estimated_ram_mb=CATALOG[model].get('estimated_ram_mb'),
        production_stt_rss_mb=body.get('production_stt_rss_mb'),
    )
    if not budget['sufficient_for_load']:
        raise MemoryError(budget['insufficient_reason'] or 'Memória insuficiente para carregar o modelo')

    engine = _create_configured_backend(
        model, location(root, model, variant), body.get('threads', 2), budget['memory_limit_mb'], body
    )
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
                'memory_budget': budget,
            }
        return _execute_test_on_engine(
            body,
            engine=engine,
            manifest=manifest,
            root=root,
            cold_load_ms=float(engine.load_ms or 0.0),
            session_reused=False,
            memory_budget=budget,
        )
    finally:
        engine.close()


def _format_exception_payload(exc: BaseException, *, stage: str = 'infer', memory_budget: dict[str, Any] | None = None) -> dict[str, Any]:
    status = getattr(getattr(exc, 'response', None), 'status_code', None)
    structured_code = getattr(exc, 'code', None)
    failure_detail = None

    if type(status) is int:
        error_code = 'download_http_error'
        message = 'Download recusado (HTTP ' + str(status) + '); tente novamente após verificar acesso à origem.'
    elif isinstance(exc, MemoryError):
        failure_detail = classify_memory_failure(
            exc=exc,
            observed_rss_mb=round(_process_tree_rss_mb(), 2),
            internal_limit_mb=(memory_budget or {}).get('memory_limit_mb'),
            effective_available_mb=(memory_budget or {}).get('effective_available_mb'),
            stage=stage,
        )
        error_code = 'oom'
        message = failure_detail['reason']
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
        'Política de memória inválida',
        'Limite de memória personalizado',
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
        }.get(type(exc).__name__, f'Falha de carregamento ou inferência ({type(exc).__name__}): {str(exc)[:180]}')

    out: dict[str, Any] = {'error': type(exc).__name__, 'error_code': error_code, 'message': message}
    if failure_detail is not None:
        out['failure_detail'] = failure_detail
    return out


def _serve_main() -> int:
    """Persistent JSON-RPC loop holding at most one resident AI Lab STT model."""
    session: dict[str, Any] = {
        'engine': None,
        'key': None,
        'model': None,
        'variant': None,
        'threads': None,
        'manifest': None,
        'cold_load_ms': 0.0,
        'warmup_ms': 0.0,
        'warmed_up': False,
        'infer_count': 0,
        'loaded_at': None,
        'memory_budget': None,
        'last_error': None,
        'failure_detail': None,
    }

    def _unload() -> dict[str, Any]:
        before_mb = round(max(_process_tree_rss_mb(), _safe_engine_rss_mb(session['engine'])), 2)
        eng = session['engine']
        session['engine'] = None
        session['key'] = None
        session['model'] = None
        session['variant'] = None
        session['threads'] = None
        session['manifest'] = None
        session['cold_load_ms'] = 0.0
        session['warmup_ms'] = 0.0
        session['warmed_up'] = False
        session['infer_count'] = 0
        session['loaded_at'] = None
        if eng is not None:
            try:
                eng.close()
            except Exception:
                pass
        gc.collect()
        after_mb = round(_process_tree_rss_mb(), 2)
        return {
            'ok': True,
            'state': 'unloaded',
            'ram_before_mb': before_mb,
            'ram_after_mb': after_mb,
            'freed_mb': round(max(0.0, before_mb - after_mb), 2),
        }

    def _session_key(req: dict[str, Any], budget: dict[str, Any]) -> tuple:
        model = req.get('model', 'fastconformer')
        variant = req.get('variant', 'adapted') if model == 'fastconformer' else None
        threads = int(req.get('threads', 2))
        prof = (req.get('nemotron_streaming_profile') or req.get('nemotron_profile') or 'offline') if model == 'nemotron_unified_0_6b' else None
        limit_key = budget['memory_limit_mb'] if budget['memory_policy'] == 'custom' else None
        return (model, variant, threads, budget['memory_policy'], limit_key, prof)

    def _status_payload(extra_budget: dict[str, Any] | None = None) -> dict[str, Any]:
        alive = _is_engine_alive(session['engine'])
        if session['engine'] is not None and not alive:
            _unload()
        rss_mb = round(max(_process_tree_rss_mb(), _safe_engine_rss_mb(session['engine'])), 2)
        budget = extra_budget or session['memory_budget'] or compute_memory_budget(policy='auto')
        return {
            'ok': True,
            'state': 'resident' if alive else ('error' if session['last_error'] else 'unloaded'),
            'model': session['model'] if alive else None,
            'variant': session['variant'] if alive else None,
            'threads': session['threads'] if alive else None,
            'cold_load_ms': session['cold_load_ms'] if alive else None,
            'warmup_ms': session['warmup_ms'] if alive else None,
            'warmed_up': session['warmed_up'] if alive else False,
            'infer_count': session['infer_count'] if alive else 0,
            'process_rss_mb': rss_mb,
            'memory_budget': budget,
            'last_error': session['last_error'],
            'failure_detail': session['failure_detail'],
        }

    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        req: dict[str, Any] = {}
        stage = 'runtime'
        active_budget = session.get('memory_budget')
        try:
            req = json.loads(line)
            cmd = req.get('cmd') or req.get('command')
            if cmd == 'status':
                budget = compute_memory_budget(
                    policy=req.get('memory_policy', 'auto'),
                    custom_limit_mb=req.get('memory_limit_mb'),
                    production_stt_rss_mb=req.get('production_stt_rss_mb'),
                )
                print(json.dumps(_status_payload(budget), ensure_ascii=True, allow_nan=False), flush=True)
            elif cmd == 'unload':
                session['last_error'] = None
                session['failure_detail'] = None
                print(json.dumps(_unload(), ensure_ascii=True, allow_nan=False), flush=True)
            elif cmd == 'shutdown':
                _unload()
                print(json.dumps({'ok': True, 'state': 'stopped'}), flush=True)
                return 0
            elif cmd == 'load':
                stage = 'load'
                root = Path(req['root'])
                model = req.get('model', 'fastconformer')
                variant = req.get('variant', 'adapted')
                threads = int(req.get('threads', 2))
                active_budget = compute_memory_budget(
                    policy=req.get('memory_policy', 'auto'),
                    custom_limit_mb=req.get('memory_limit_mb'),
                    catalog_limit_mb=CATALOG[model].get('memory_limit_mb'),
                    estimated_ram_mb=CATALOG[model].get('estimated_ram_mb'),
                    production_stt_rss_mb=req.get('production_stt_rss_mb'),
                )
                key = _session_key(req, active_budget)
                if _is_engine_alive(session['engine']) and session['key'] == key:
                    session['last_error'] = None
                    session['failure_detail'] = None
                    payload = _status_payload(active_budget)
                    payload.update(reused=True, cold_load_ms=0.0, session_cold_load_ms=session['cold_load_ms'])
                    print(json.dumps(payload, ensure_ascii=True, allow_nan=False), flush=True)
                    continue

                if not active_budget['sufficient_for_load']:
                    raise MemoryError(active_budget['insufficient_reason'] or 'Memória insuficiente para carregar modelo')

                _unload()
                manifest = verify(root, model, variant)
                with redirect_stdout(sys.stderr):
                    engine = _create_configured_backend(
                        model, location(root, model, variant), threads, active_budget['memory_limit_mb'], req
                    )
                    engine.load()
                warmup_ms = 0.0
                warmed = False
                if req.get('warmup'):
                    import numpy as np

                    with redirect_stdout(sys.stderr):
                        _, warmup_ms = engine.recognize(np.sin(np.linspace(0, 120, 16000)).astype(np.float32) * 0.15)
                    warmed = True

                session.update({
                    'engine': engine,
                    'key': key,
                    'model': model,
                    'variant': variant if model == 'fastconformer' else None,
                    'threads': threads,
                    'manifest': manifest,
                    'cold_load_ms': round(float(engine.load_ms or 0.0), 3),
                    'warmup_ms': round(float(warmup_ms), 3),
                    'warmed_up': warmed,
                    'infer_count': 0,
                    'loaded_at': time.monotonic(),
                    'memory_budget': active_budget,
                    'last_error': None,
                    'failure_detail': None,
                })
                payload = _status_payload(active_budget)
                payload.update(reused=False, session_cold_load_ms=session['cold_load_ms'])
                print(json.dumps(payload, ensure_ascii=True, allow_nan=False), flush=True)
            elif cmd == 'warmup':
                stage = 'warmup'
                if not _is_engine_alive(session['engine']):
                    raise RuntimeError('Nenhum modelo residente carregado no AI Lab para aquecer')
                import numpy as np

                with redirect_stdout(sys.stderr):
                    _, w_ms = session['engine'].recognize(np.sin(np.linspace(0, 120, 16000)).astype(np.float32) * 0.15)
                session['warmup_ms'] = round(float(w_ms), 3)
                session['warmed_up'] = True
                session['last_error'] = None
                session['failure_detail'] = None
                payload = _status_payload()
                payload.update(warmup_ms=session['warmup_ms'])
                print(json.dumps(payload, ensure_ascii=True, allow_nan=False), flush=True)
            elif cmd == 'test':
                stage = 'infer'
                _validate_test_params(req)
                root = Path(req['root'])
                model = req.get('model', 'fastconformer')
                variant = req.get('variant', 'adapted')
                threads = int(req.get('threads', 2))
                active_budget = compute_memory_budget(
                    policy=req.get('memory_policy', 'auto'),
                    custom_limit_mb=req.get('memory_limit_mb'),
                    catalog_limit_mb=CATALOG[model].get('memory_limit_mb'),
                    estimated_ram_mb=CATALOG[model].get('estimated_ram_mb'),
                    production_stt_rss_mb=req.get('production_stt_rss_mb'),
                )
                key = _session_key(req, active_budget)
                exec_mode = str(req.get('execution_mode') or ('warm' if _is_engine_alive(session['engine']) and session['key'] == key else 'cold'))
                force_cold = exec_mode == 'cold'

                reused = False
                cold_ms = 0.0
                if not force_cold and _is_engine_alive(session['engine']) and session['key'] == key:
                    reused = True
                else:
                    stage = 'load'
                    if not active_budget['sufficient_for_load']:
                        raise MemoryError(active_budget['insufficient_reason'] or 'Memória insuficiente para carregar modelo')
                    _unload()
                    manifest = verify(root, model, variant)
                    with redirect_stdout(sys.stderr):
                        engine = _create_configured_backend(
                            model, location(root, model, variant), threads, active_budget['memory_limit_mb'], req
                        )
                        engine.load()
                    cold_ms = round(float(engine.load_ms or 0.0), 3)
                    session.update({
                        'engine': engine,
                        'key': key,
                        'model': model,
                        'variant': variant if model == 'fastconformer' else None,
                        'threads': threads,
                        'manifest': manifest,
                        'cold_load_ms': cold_ms,
                        'warmup_ms': 0.0,
                        'warmed_up': False,
                        'infer_count': 0,
                        'loaded_at': time.monotonic(),
                        'memory_budget': active_budget,
                    })

                stage = 'infer'
                with redirect_stdout(sys.stderr):
                    res = _execute_test_on_engine(
                        req,
                        engine=session['engine'],
                        manifest=session['manifest'],
                        root=root,
                        cold_load_ms=cold_ms,
                        session_reused=reused,
                        memory_budget=active_budget,
                    )
                session['infer_count'] += 1
                session['last_error'] = None
                session['failure_detail'] = None
                if req.get('resident', True) is False:
                    _unload()
                    res['resident_after_test'] = False
                else:
                    res['resident_after_test'] = True
                res['ok'] = True
                print(json.dumps(res, ensure_ascii=True, allow_nan=False), flush=True)
            else:
                print(json.dumps({'ok': False, 'error_code': 'validation_error', 'message': f'Comando desconhecido: {cmd}'}), flush=True)
        except Exception as exc:
            err_payload = _format_exception_payload(exc, stage=stage, memory_budget=active_budget)
            err_payload['ok'] = False
            session['last_error'] = err_payload['message']
            session['failure_detail'] = err_payload.get('failure_detail')
            if isinstance(exc, MemoryError) or not _is_engine_alive(session['engine']):
                _unload()
            print(json.dumps(err_payload, ensure_ascii=True, allow_nan=False), flush=True)
    _unload()
    return 0


if __name__ == '__main__':
    if '--serve' in sys.argv:
        raise SystemExit(_serve_main())
    try:
        body = json.loads(sys.stdin.buffer.read(2_097_153))
        with redirect_stdout(sys.stderr):
            result = run(body)
        print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    except Exception as exc:
        print(json.dumps(_format_exception_payload(exc)))
        sys.exit(1)
