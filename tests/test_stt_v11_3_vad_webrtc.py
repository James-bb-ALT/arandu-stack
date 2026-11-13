from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
from pathlib import Path
import sys
import types
import wave

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / 'arandu_stack' / 'app'
STT_SRC = ROOT / 'arandu_stack' / 'rootfs' / 'modules' / 'arandu_stt' / 'rootfs' / 'src'
for p in (str(APP_DIR), str(STT_SRC), str(STT_SRC / 'arandu_stt')):
    if p not in sys.path:
        sys.path.insert(0, p)


def _wav_bytes(samples: np.ndarray, rate: int = 16000, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        i16 = np.clip(np.round(samples * 32767.0), -32768, 32767).astype('<i2')
        wf.writeframes(i16.tobytes())
    return buf.getvalue()


def test_audio_processor_resampling_and_aec_reference_gating(monkeypatch):
    from arandu_stt.audio_processor import AudioProcessor, AudioProcessorConfig, PRESETS

    assert set(PRESETS) == {'original', 'vad', 'balanced', 'custom'}
    assert PRESETS['original']['vad_enabled'] is False
    assert PRESETS['vad']['vad_enabled'] is True
    assert PRESETS['balanced']['high_pass_filter'] is True
    assert PRESETS['balanced']['noise_suppression'] is True

    with pytest.raises(ValueError):
        AudioProcessorConfig(sample_rate=22050)
    with pytest.raises(ValueError):
        AudioProcessorConfig(ns_level=9)

    # Resampling 48 kHz stereo -> 16 kHz mono
    t48 = np.linspace(0, 0.2, 9600, endpoint=False, dtype=np.float32)
    stereo = np.column_stack([np.sin(2 * np.pi * 440 * t48), np.sin(2 * np.pi * 440 * t48)]).reshape(-1)
    pcm48 = np.clip(np.round(stereo * 32767.0), -32768, 32767).astype('<i2').tobytes()

    ap_plain = AudioProcessor(AudioProcessorConfig(sample_rate=48000, num_channels=2))
    out, meta = ap_plain.process_pcm16(pcm48)
    assert out.dtype == np.float32
    assert 3100 <= len(out) <= 3300
    assert meta['resampled_from_hz'] == 48000

    # Mock pywebrtc_audio to test HPF/NS/AGC + AEC3 far-end reference gating
    calls = {'reverse': 0, 'stream': 0}

    class FakeNativeAP:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def process_reverse_stream(self, frame: bytes):
            calls['reverse'] += 1
            return frame

        def process_stream(self, frame: bytes):
            calls['stream'] += 1
            return frame

    fake_mod = types.ModuleType('pywebrtc_audio')
    fake_mod.AudioProcessor = FakeNativeAP
    monkeypatch.setitem(sys.modules, 'pywebrtc_audio', fake_mod)

    ap_aec = AudioProcessor(AudioProcessorConfig(
        sample_rate=16000, num_channels=1, high_pass_filter=True,
        noise_suppression=True, ns_level=2, auto_gain_control=True, echo_cancellation=True,
    ))
    pcm16 = (np.zeros(1600, dtype='<i2')).tobytes()  # 100 ms = 10 frames

    # Without far_end_pcm: AEC must NOT pretend to be active
    _, meta_no_ref = ap_aec.process_pcm16(pcm16)
    assert meta_no_ref['aec']['requested'] is True
    assert meta_no_ref['aec']['reference_available'] is False
    assert meta_no_ref['aec']['active'] is False
    assert 'far-end' in (meta_no_ref['aec']['reason'] or '')
    assert calls['stream'] == 10
    assert calls['reverse'] == 0

    # With far_end_pcm: AEC becomes active and feeds reverse stream
    _, meta_with_ref = ap_aec.process_pcm16(pcm16, far_end_pcm=pcm16)
    assert meta_with_ref['aec']['reference_available'] is True
    assert meta_with_ref['aec']['active'] is True
    assert calls['reverse'] == 10


def test_silero_vad_state_machine_preroll_postroll_and_sha256(tmp_path, monkeypatch):
    from arandu_stt import vad_processor as vp

    vad_file = tmp_path / 'silero_vad.onnx'
    vad_file.write_bytes(b'fake-onnx-model')
    bad_sha = hashlib.sha256(b'fake-onnx-model').hexdigest()
    assert bad_sha != vp.SILERO_VAD_SHA256

    # Reject wrong SHA-256
    proc_bad = vp.SileroVADProcessor(vad_file)
    with pytest.raises(ValueError, match='SHA-256'):
        proc_bad.load()

    # Patch expected SHA-256 for unit test and mock onnxruntime
    monkeypatch.setattr(vp, 'SILERO_VAD_SHA256', bad_sha)

    class FakeSession:
        def run(self, _outputs, feed):
            inp = feed['input'][0, vp.CONTEXT_SAMPLES:]
            energy = float(np.sqrt(np.mean(inp ** 2)))
            prob = 0.9 if energy > 0.05 else 0.02
            return np.array([[prob]], dtype=np.float32), feed['state'] + 1.0

    fake_ort = types.ModuleType('onnxruntime')
    fake_ort.SessionOptions = lambda: types.SimpleNamespace(intra_op_num_threads=1, inter_op_num_threads=1)
    fake_ort.InferenceSession = lambda *a, **kw: FakeSession()
    monkeypatch.setitem(sys.modules, 'onnxruntime', fake_ort)

    cfg = vp.VadConfig(threshold=0.5, min_speech_ms=160, min_silence_ms=320, pre_roll_ms=160, post_roll_ms=128)
    vad = vp.SileroVADProcessor(vad_file, cfg)

    # Pure silence -> suppressed
    silence = np.zeros(16000, dtype=np.float32)
    res_sil = vad.process_audio(silence)
    assert res_sil.speech_detected is False
    assert len(res_sil.audio) == 0

    # 400ms silence + 300ms speech + 150ms brief pause (tolerated) + 300ms speech + 500ms silence
    seg_sil1 = np.zeros(int(0.4 * 16000), dtype=np.float32)
    seg_sp1 = np.full(int(0.3 * 16000), 0.2, dtype=np.float32)
    seg_pause = np.zeros(int(0.15 * 16000), dtype=np.float32)
    seg_sp2 = np.full(int(0.3 * 16000), 0.2, dtype=np.float32)
    seg_sil2 = np.zeros(int(0.5 * 16000), dtype=np.float32)
    utterance = np.concatenate([seg_sil1, seg_sp1, seg_pause, seg_sp2, seg_sil2])

    res_sp = vad.process_audio(utterance)
    assert res_sp.speech_detected is True
    assert len(res_sp.segments) == 1  # pause was < min_silence_ms so kept unified
    assert len(res_sp.audio) < len(utterance)
    assert res_sp.end_of_speech_ms is not None

    # Concurrent session isolation
    vad2 = vp.SileroVADProcessor(vad_file, cfg)
    assert vad2.process_audio(silence).speech_detected is False
    assert vad._state is not vad2._state


def test_new_stt_backends_contracts_and_missing_runtimes(tmp_path, monkeypatch):
    from arandu_stt.parakeet_ultra_backend import ParakeetUltraBackend, PARAKEET_ULTRA_FILES
    from arandu_stt.phonon_backend import PhononBackend
    from arandu_stt.nemotron_unified_backend import NemotronUnifiedBackend, STREAMING_PROFILES
    from arandu_stt.backends import create_backend

    # 1. Parakeet Ultra Q8 & Q4
    p_dir = tmp_path / 'parakeet_ultra'
    p_dir.mkdir()
    gguf_q8 = p_dir / PARAKEET_ULTRA_FILES['parakeet_ultra_q8']
    gguf_q8.write_bytes(b'x' * 10_000_001)
    assert ParakeetUltraBackend(p_dir, model_id='parakeet_ultra_q8').filename == 'parakeet-ultra-Q8_0.gguf'
    assert ParakeetUltraBackend(p_dir, model_id='parakeet_ultra_q4').filename == 'parakeet-ultra-Q4_K_M.gguf'

    # Explicit error if runtime missing
    monkeypatch.setitem(sys.modules, 'transcribe_cpp', None)
    b_p = create_backend('parakeet_ultra_q8', p_dir, threads=2)
    with pytest.raises((ImportError, RuntimeError)):
        b_p.load()

    # Fake persistent transcribe_cpp
    loaded = {'count': 0}

    class FakeSession:
        def run(self, audio, **kwargs):
            return types.SimpleNamespace(text='não desligue a luz da sala')

        def close(self):
            pass

    class FakeModel:
        capabilities = types.SimpleNamespace(languages=['pt', 'en'])

        def __init__(self, path, **kwargs):
            loaded['count'] += 1

        def session(self, n_threads=2):
            return FakeSession()

        def close(self):
            pass

    fake_tc = types.ModuleType('transcribe_cpp')
    fake_tc.Model = FakeModel
    monkeypatch.setitem(sys.modules, 'transcribe_cpp', fake_tc)
    b_p.load()
    assert loaded['count'] == 1
    text, ms = b_p.recognize(np.zeros(1600, dtype=np.float32))
    assert text == 'não desligue a luz da sala'
    assert ms >= 0.0
    b_p.close()

    # 2. Phonon 2.5
    ph_dir = tmp_path / 'phonon_2_5'
    ph_dir.mkdir()
    (ph_dir / 'phonon-2.5.fsq').write_bytes(b'x' * 10_000_001)
    fake_fr = types.ModuleType('fermion_research')
    fake_fr.load_model = lambda path, **kw: types.SimpleNamespace(transcribe=lambda audio, **k: {'text': 'acende o quarto'})
    monkeypatch.setitem(sys.modules, 'fermion_research', fake_fr)
    b_ph = create_backend('phonon_2_5', ph_dir, threads=2)
    assert type(b_ph).__name__ == 'PhononBackend'
    b_ph.load()
    text_ph, _ = b_ph.recognize(np.zeros(1600, dtype=np.float32))
    assert text_ph == 'acende o quarto'
    b_ph.close()

    # 3. Nemotron Unified 0.6B offline & streaming profiles
    assert set(STREAMING_PROFILES) == {'precision', 'balanced', 'fast', 'experimental'}
    nem_dir = tmp_path / 'nemotron_unified_0_6b'
    nem_dir.mkdir()
    with (nem_dir / 'nemotron-labs-unified-asr-0.6b.nemo').open('wb') as fh:
        fh.truncate(100_000_001)

    class FakeNemoASRModel:
        @staticmethod
        def restore_from(restore_path, map_location='cpu'):
            return types.SimpleNamespace(
                eval=lambda: None,
                transcribe=lambda paths, target_lang='pt', **kw: [types.SimpleNamespace(text='liga a tomada')],
            )

    fake_torch = types.ModuleType('torch')
    fake_torch.set_num_threads = lambda n: None
    fake_nemo = types.ModuleType('nemo')
    fake_nemo_col = types.ModuleType('nemo.collections')
    fake_nemo_asr = types.ModuleType('nemo.collections.asr')
    fake_nemo_asr.models = types.SimpleNamespace(ASRModel=FakeNemoASRModel)

    b_nem_missing = NemotronUnifiedBackend(nem_dir, streaming_profile='balanced')
    with pytest.raises(ImportError, match='NeMo'):
        b_nem_missing.load()

    monkeypatch.setitem(sys.modules, 'torch', fake_torch)
    monkeypatch.setitem(sys.modules, 'nemo', fake_nemo)
    monkeypatch.setitem(sys.modules, 'nemo.collections', fake_nemo_col)
    monkeypatch.setitem(sys.modules, 'nemo.collections.asr', fake_nemo_asr)

    b_nem = NemotronUnifiedBackend(nem_dir, streaming_profile='balanced')
    b_nem.load()
    text_nem, _ = b_nem.recognize(np.zeros(3200, dtype=np.float32))
    assert text_nem == 'liga a tomada'
    sess = b_nem.create_stream('fast')
    assert sess.profile == 'fast'
    assert sess.algorithmic_latency_ms == 240  # 80 + 160 ms
    step = b_nem.feed_chunk(sess, np.zeros(1280, dtype=np.float32))
    assert step['ready_for_step'] is True
    done = b_nem.finish_stream(sess)
    assert done['text'] == 'liga a tomada'
    b_nem.close()


def test_catalog_remove_settings_defaults_and_benchmark_negation(tmp_path, monkeypatch):
    from arandu_stt import model_catalog
    import modules as app_modules
    import settings as app_settings
    import stt_lab

    # Production defaults must keep VAD & WebRTC AP disabled
    assert app_modules.DEFAULTS['stt']['vad_enabled'] is False
    assert app_modules.DEFAULTS['stt']['high_pass_filter'] is False
    assert app_modules.DEFAULTS['stt']['noise_suppression'] is False
    assert app_modules.DEFAULTS['stt']['auto_gain_control'] is False
    assert app_modules.DEFAULTS['stt']['echo_cancellation'] is False
    assert app_modules.DEFAULTS['stt']['stt_model'] == 'orukeet_int8'
    field_keys = {f['id'] for f in app_settings.FIELDS['stt']}
    assert {'vad_enabled', 'vad_threshold', 'noise_suppression', 'ns_level', 'nemotron_streaming_profile'} <= field_keys

    # Catalog includes all 4 new models and supports remove()
    for mid in ('parakeet_ultra_q8', 'parakeet_ultra_q4', 'phonon_2_5', 'nemotron_unified_0_6b'):
        assert mid in model_catalog.CATALOG
    target_dir = model_catalog.location(tmp_path, 'phonon_2_5')
    target_dir.mkdir(parents=True)
    weights = target_dir / 'phonon-2.5.fsq'
    weights.write_bytes(b'fake-weights')
    fake_sha = hashlib.sha256(b'fake-weights').hexdigest()
    monkeypatch.setitem(model_catalog.CATALOG['phonon_2_5'], 'sha256', {'phonon-2.5.fsq': fake_sha})
    (target_dir / 'manifest.json').write_text(json.dumps({
        'revision': model_catalog.CATALOG['phonon_2_5']['revision'],
        'sha256': {'phonon-2.5.fsq': fake_sha},
    }), encoding='utf-8')
    assert model_catalog.describe(tmp_path, 'phonon_2_5')['installed'] is True
    assert model_catalog.remove(tmp_path, 'phonon_2_5')['removed'] is True
    assert model_catalog.describe(tmp_path, 'phonon_2_5')['installed'] is False

    # Quality / negation preservation check
    from arandu_stt import stt_processing
    q = stt_processing.evaluate('não desligue a luz da sala', 'não desligue a luz da sala', 'não desligue a luz da sala')
    assert q['evaluated'] is True
    assert q['wer_final'] == 0.0
    assert q['negation_preserved_final'] is True
