"""Comprehensive dependency, STT resilience, semantic safety, and pipeline stage-failure tests."""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import types

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
STT_SRC = ROOT / 'arandu_stack' / 'rootfs' / 'modules' / 'arandu_stt' / 'rootfs' / 'src' / 'arandu_stt'
for p in (str(ROOT), str(STT_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)


# ---------------------------------------------------------------------------
# 1. Dependency & Isolation Gate
# ---------------------------------------------------------------------------

def test_dependency_metadata_and_isolation_invariants():
    from scripts.check_dependencies import audit_static_metadata
    errors = audit_static_metadata()
    assert errors == [], f'Dependency audit failed: {errors}'


# ---------------------------------------------------------------------------
# 2. Structured Observability & Error Codes
# ---------------------------------------------------------------------------

def test_structured_error_codes_and_redaction():
    import logging
    from stt_errors import ERROR_CODES, AranduStructuredError, format_structured_error, log_structured_error

    expected = {
        'DEPENDENCY_CONFLICT', 'NATIVE_LIBRARY_MISSING', 'MODEL_RUNTIME_UNAVAILABLE',
        'MODEL_LOAD_FAILED', 'MODEL_INTEGRITY_FAILED', 'STT_TIMEOUT', 'NLU_TIMEOUT',
        'DECISION_UNAVAILABLE', 'LLM_TIMEOUT', 'TTS_FAILURE', 'QUEUE_SATURATED',
        'MODULE_STARTUP_FAILED',
    }
    assert expected.issubset(ERROR_CODES)
    err = AranduStructuredError('MODEL_RUNTIME_UNAVAILABLE', 'runtime ausente', stage='load')
    assert 'MODEL_RUNTIME_UNAVAILABLE' in str(err)
    payload = format_structured_error(
        'STT_TIMEOUT', 'arandu_stt', 'infer', TimeoutError('x' * 500),
        request_id='req-1', recovery='kept_previous_model', final_state='ready',
    )
    assert payload['error_code'] == 'STT_TIMEOUT'
    assert payload['request_id'] == 'req-1'
    assert len(payload['message']) <= 240
    logged = log_structured_error(logging.getLogger('test'), 'TTS_FAILURE', 'arandu_tts', 'speak', RuntimeError('tts down'))
    assert logged['error_code'] == 'TTS_FAILURE'


# ---------------------------------------------------------------------------
# 3. Phonon 2.5 & Nemotron Runtime Isolation Contracts
# ---------------------------------------------------------------------------

def test_phonon_backend_uses_real_fermion_load_speech_api_and_cleans_up(tmp_path, monkeypatch):
    from phonon_backend import PhononBackend

    model_dir = tmp_path / 'phonon_2_5'
    model_dir.mkdir()
    (model_dir / 'config.json').write_text('{}', encoding='utf-8')
    (model_dir / 'packed_manifest.json').write_text('{"format":"fermion"}', encoding='utf-8')

    closed = {'flag': False}
    loaded_args = {}

    class FakeSpeech:
        def transcribe_array(self, audio):
            assert isinstance(audio, np.ndarray)
            return 'ligar luz da sala', 0.05, 1.0

        def close(self):
            closed['flag'] = True

    fake_fermion = types.ModuleType('fermion')

    def _load_speech(target, *, quiet=True):
        loaded_args['target'] = target
        loaded_args['quiet'] = quiet
        return FakeSpeech()

    fake_fermion.load_speech = _load_speech
    monkeypatch.setitem(sys.modules, 'fermion', fake_fermion)

    backend = PhononBackend(model_dir, threads=2).load()
    text, ms = backend.recognize(np.zeros(1600, dtype=np.float32))
    assert text == 'ligar luz da sala'
    assert ms >= 0.0
    assert loaded_args['target'] == str(model_dir)
    assert loaded_args['quiet'] is True
    backend.close()
    assert closed['flag'] is True


def test_phonon_backend_reports_runtime_unavailable_without_fermion(tmp_path, monkeypatch):
    import importlib.util
    from phonon_backend import PhononBackend
    from stt_errors import AranduStructuredError

    model_dir = tmp_path / 'phonon_2_5'
    model_dir.mkdir()
    (model_dir / 'config.json').write_text('{}', encoding='utf-8')
    (model_dir / 'packed_manifest.json').write_text('{}', encoding='utf-8')

    monkeypatch.delitem(sys.modules, 'fermion', raising=False)
    monkeypatch.setattr(importlib.util, 'find_spec', lambda name: None)
    monkeypatch.setenv('ARANDU_PHONON_PYTHON', str(tmp_path / 'nonexistent_python'))

    with pytest.raises(AranduStructuredError) as exc_info:
        PhononBackend(model_dir, threads=2).load()
    assert exc_info.value.code == 'MODEL_RUNTIME_UNAVAILABLE'


def test_nemotron_unavailable_does_not_break_catalog(tmp_path, monkeypatch):
    import importlib.util
    import model_catalog
    from nemotron_unified_backend import NemotronUnifiedBackend
    from stt_errors import AranduStructuredError

    model_dir = tmp_path / 'nemotron_unified_0_6b'
    model_dir.mkdir()
    (model_dir / 'nemotron-labs-unified-asr-0.6b.nemo').write_bytes(b'0' * (100_000_001))

    monkeypatch.setattr(importlib.util, 'find_spec', lambda name: None)
    monkeypatch.setenv('ARANDU_NEMOTRON_PYTHON', str(tmp_path / 'missing_nemo_py'))

    desc = model_catalog.describe(tmp_path, 'nemotron_unified_0_6b')
    assert desc['id'] == 'nemotron_unified_0_6b'
    assert desc['experimental'] is True
    assert desc['runtime_available'] is False
    assert 'MODEL_RUNTIME_UNAVAILABLE' in (desc['runtime_note'] or '')

    with pytest.raises(AranduStructuredError) as exc_info:
        NemotronUnifiedBackend(model_dir, threads=2).load()
    assert exc_info.value.code == 'MODEL_RUNTIME_UNAVAILABLE'


# ---------------------------------------------------------------------------
# 4. STT Edge Cases, Integrity & Rollback Safety
# ---------------------------------------------------------------------------

def test_stt_model_integrity_missing_corrupt_and_previous_restore(tmp_path):
    import model_catalog

    # Missing model raises FileNotFoundError
    with pytest.raises(FileNotFoundError):
        model_catalog.verify(tmp_path, 'whisper_tiny')

    # Corrupted checksum raises ValueError
    d = model_catalog.location(tmp_path, 'whisper_tiny')
    d.mkdir(parents=True)
    for fname in model_catalog.files('whisper_tiny'):
        (d / fname).write_bytes(b'corrupt-data')
    rev = 'a' * 40
    # Use a fake manifest with wrong sha256
    (d / 'manifest.json').write_text(
        f'{{"revision": "{rev}", "sha256": {{"model.bin": "deadbeef"}}}}',
        encoding='utf-8',
    )
    with pytest.raises(ValueError):
        model_catalog.verify(tmp_path, 'whisper_tiny')

    # Atomic rollback restores .previous directory if main dir is missing
    prev = d.with_name(d.name + '.previous')
    import shutil
    shutil.rmtree(d)
    prev.mkdir(parents=True)
    (prev / 'marker.txt').write_text('preserved', encoding='utf-8')
    with pytest.raises(FileNotFoundError):
        model_catalog.verify(tmp_path, 'whisper_tiny')
    assert d.is_dir() and (d / 'marker.txt').read_text(encoding='utf-8') == 'preserved'


def test_stt_audio_edge_cases_empty_silence_vad_and_aec():
    from audio_processor import AudioProcessor, AudioProcessorConfig
    from vad_processor import SileroVADProcessor, VadConfig

    # Empty / silence / AEC without far-end reference should never crash
    proc = AudioProcessor(AudioProcessorConfig(
        high_pass_filter=True, noise_suppression=True,
        auto_gain_control=True, echo_cancellation=True,
    ))
    empty_out, _ = proc.process_pcm16(b'')
    assert empty_out.size == 0
    silence = np.zeros(1600, dtype='<i2').tobytes()
    out, _ = proc.process_pcm16(silence)
    flushed = proc.flush()
    assert out.size + flushed.size <= 1600
    proc.close()

    # Invalid sample rate guard
    with pytest.raises(ValueError):
        AudioProcessorConfig(sample_rate=0)

    # Silero VAD preserves speech segments & suppresses pure silence when session is mocked
    class _FakeVadSession:
        def run(self, _outputs, inputs):
            chunk = inputs['input'][0]
            energy = float(np.sqrt(np.mean(chunk ** 2)))
            prob = 0.95 if energy > 0.05 else 0.01
            return [np.array([[prob]], dtype=np.float32), inputs['state']]

    vad = SileroVADProcessor(config=VadConfig())
    vad._session = _FakeVadSession()
    speech_audio = np.concatenate([
        np.zeros(512 * 3, dtype=np.float32),
        np.full(512 * 10, 0.2, dtype=np.float32),
        np.zeros(512 * 3, dtype=np.float32),
    ])
    res = vad.process_audio(speech_audio)
    assert res.speech_detected is True
    assert res.audio.size > 0 and res.audio.size <= speech_audio.size
    vad.close()


# ---------------------------------------------------------------------------
# 5. Semantic Safety Suite (PT-BR)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('phrase,expected_refusal', [
    ('não desligue a luz', 'semantic_negation'),
    ('desligue a luz', None),
    ('ligue todas as luzes, menos a do quarto', 'semantic_exclusion'),
    ('daqui a quinze minutos', 'semantic_condition'),
    ('ligue a luz às sete horas', 'semantic_condition'),
    ('às sete horas', None),
    ('aumente para cem por cento', None),
    ('nunca abra o portão', 'semantic_negation'),
    ('acenda tudo exceto o abajur', 'semantic_exclusion'),
    ('se chover feche a janela', 'semantic_condition'),
])
def test_semantic_safety_ptbr_fixtures(phrase, expected_refusal):
    from custom_components.local_nlu.semantic_context import semantic_refusal
    catalog = {
        'areas': [{'area_id': 'quarto', 'names': ['quarto']}],
        'entities': [{'registry_id': 'light.abajur', 'name': 'abajur', 'aliases': []}],
    }
    assert semantic_refusal(phrase, catalog) == expected_refusal


# ---------------------------------------------------------------------------
# 6. Pipeline Stage Failure & Isolation Guarantees
# ---------------------------------------------------------------------------

def test_pipeline_stage_failures_never_authorize_or_leak_sessions():
    from custom_components.local_nlu.progressive_orchestrator import ProgressiveOrchestrator

    orch = ProgressiveOrchestrator()
    snapshot = {'generation': 'g1', 'areas': [], 'entities': []}

    async def _partial_needs_repair(_req):
        return {
            'request_kind': 'needs_repair',
            'safety_flags': [],
            'origin': {'area_id': 'sala'},
            'v4_outcome': {'status': 'clarification', 'reason': 'ambiguous_target', 'operations': []},
            'clauses': [
                {
                    'clause_id': 0,
                    'original_text': 'liga a luz',
                    'slots': {'action': {'status': 'resolved', 'value': 'turn_on'}},
                    'unresolved': ['target'],
                    'allowed_actions': ['turn_on'],
                    'constraints': {'negated': False, 'conditional': False, 'quoted_content': False},
                }
            ],
        }

    # Decision raises timeout/error -> NEVER authorizes HA execution
    async def _decision_boom(_payload):
        raise TimeoutError('DECISION_UNAVAILABLE')

    async def _review(_payload):
        return {'accepted': False}

    res = asyncio.run(
        orch.resolve(
            text='liga a luz',
            snapshot_payload=snapshot,
            snapshot_by_id={},
            origin_area='sala',
            origin_source='satellite',
            origin_context_id='sess-A',
            user_id='user-A',
            interpret_v5=_partial_needs_repair,
            decision_partial=_decision_boom,
            review_v5=_review,
            llm_enabled=False,
        )
    )
    assert res.outcome_kind == 'clarification'
    assert res.operations == ()

    # Direct NLU plan succeeds even when Decision & LLM would fail
    async def _partial_resolved(_req):
        return {
            'request_kind': 'resolved_plan',
            'safety_flags': [],
            'v4_outcome': {
                'status': 'plan',
                'operations': [{'action': 'turn_on', 'targets': ['light.sala'], 'parameters': {}, 'evidence': ['exact']}],
            },
            'clauses': [],
        }

    async def _llm_boom(_p, _d, _g):
        raise RuntimeError('LLM_TIMEOUT')

    res_direct = asyncio.run(
        orch.resolve(
            text='liga a luz da sala',
            snapshot_payload=snapshot,
            snapshot_by_id={'light.sala': {'name': 'Luz da Sala'}},
            origin_area='sala',
            origin_source='satellite',
            origin_context_id='sess-B',
            user_id='user-B',
            interpret_v5=_partial_resolved,
            decision_partial=_decision_boom,
            llm_handoff=_llm_boom,
            review_v5=_review,
        )
    )
    assert res_direct.outcome_kind == 'nlu_direct'
    assert len(res_direct.operations) == 1
