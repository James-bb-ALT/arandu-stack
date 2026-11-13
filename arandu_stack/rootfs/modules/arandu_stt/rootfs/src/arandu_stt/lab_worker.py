"""Bounded subprocess protocol for model management and acoustic experiments."""
from __future__ import annotations
import base64
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import sys
import time

from model_catalog import CATALOG, describe, install, location, remove, verify


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
        def progress(state):
            status = root / ('.install-' + model + '.json')
            staged = status.with_suffix('.tmp')
            staged.write_text(json.dumps({'state': state}), encoding='utf-8')
            staged.replace(status)
        install(root, model, variant, progress=progress, validator=validate_load)
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

        # Exact digital silence or VAD-filtered silence must never generate a hallucination.
        raw, inference_ms = ('', 0.0) if (not len(audio) or not np.any(audio)) else engine.recognize(audio)
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
        import psutil
        return {'revision': manifest.get('revision') or manifest.get('version'), 'model_sha256': manifest.get('sha256'), 'model': model, 'variant': variant if model == 'fastconformer' else None, 'state': 'ready',
                'backend': CATALOG[model]['runtime'], 'license': CATALOG[model]['license'], 'device': 'cpu', 'compute': 'fp32' if model == 'fastconformer' and variant == 'adapted' else CATALOG[model]['compute'],
                'threads': body.get('threads', 2), 'profile': profile, 'preset': preset_name,
                'model_load_ms': engine.load_ms, 'inference_ms': inference_ms, 'lexical_ms': lexical_ms,
                'preprocess_ms': ap_stats['preprocess_ms'], 'vad_ms': vad_stats['vad_ms'],
                'end_of_speech_ms': vad_stats['end_of_speech_ms'], 'speech_detected': vad_stats['speech_detected'],
                'aec': ap_stats['aec'], 'processed_wav_b64': _encode_wav_b64(audio) if len(audio) else None,
                'audio_duration_ms': len(pcm)/32, 'queue_ms': 0.0,
                'processing_ms': (time.perf_counter()-started)*1000,
                'realtime_factor': inference_ms / (len(pcm)/32),
                'estimated_ram_mb': CATALOG[model].get('estimated_ram_mb'),
                'process_rss_mb': psutil.Process(os.getpid()).memory_info().rss / 1048576,
                'raw_transcript': raw, 'final_transcript': resolved['text'],
                'safety_preserved_raw': resolved.get('safety_preserved_raw', False),
                'quality': evaluate(body.get('reference'), raw, resolved['text'], body.get('entity_reference')),
                'parameters': {'language': 'pt', 'beam_size': 1, 'vad_filter': False, 'condition_on_previous_text': False} if model.startswith('whisper_') else ({'language': 'pt', 'timestamps': 'none', 'persistent_session': True} if model in ('granite_turboctc_q4', 'parakeet_ultra_q8', 'parakeet_ultra_q4') else {'decoding_method': 'greedy_search'})}
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
        if isinstance(exc, ValueError) and str(exc) == 'Espaço insuficiente para download e instalação temporária':
            message = str(exc)
        print(json.dumps({'error': type(exc).__name__, 'message': message}))
        sys.exit(1)
