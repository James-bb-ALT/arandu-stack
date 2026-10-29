"""Testes da API Pocket com doubles de modelo (sem baixar pesos)."""
import sys
import types

import numpy as np
from arandu_tts.config import Config
from arandu_tts.pocket import PocketEngine
from arandu_tts.voices import VoiceSpec, catalogue


class Tensor:
    def detach(self): return self
    def cpu(self): return self
    def numpy(self): return np.array([0.0, -0.5, 0.5, 1.0], dtype=np.float32)


class Model:
    sample_rate = 24000
    @classmethod
    def load_model(cls, **options):
        assert options['language'] == 'portuguese'
        return cls()
    def get_state_for_audio_prompt(self, path, truncate=False):
        assert path == 'rafael' or path.endswith(('.wav', '.safetensors'))
        return {'voice': path}
    def generate_audio_stream(self, state, text):
        assert text == 'Arandu.'
        yield Tensor()


def test_pocket_default_profile_and_pcm(monkeypatch, tmp_path):
    pkg = types.ModuleType('pocket_tts')
    pkg.TTSModel = Model
    pkg.export_model_state = lambda state, target: target.write_bytes(b'profile-bytes')
    monkeypatch.setitem(sys.modules, 'pocket_tts', pkg)
    monkeypatch.setitem(sys.modules, 'torch', types.SimpleNamespace(set_num_threads=lambda n: None))
    cfg = Config(voices_dir=tmp_path)
    engine = PocketEngine(cfg)
    voice = catalogue(tmp_path)['pocket-rafael']
    chunks = list(engine.stream('Arandu.', voice))
    assert engine.loaded
    assert len(chunks) == 1
    assert chunks[0].rate == 24000
    assert chunks[0].width == 2
    assert chunks[0].data == np.array([0, -16383, 16383, 32767], dtype='<i2').tobytes()

    wav = tmp_path / 'voice.wav'
    wav.write_bytes(b'audio-placeholder')
    target = tmp_path / 'pocket' / 'minha_voz.safetensors'
    target.parent.mkdir()
    engine.prepare_voice(wav, target)
    assert target.read_bytes() == b'profile-bytes'
    new_voice = catalogue(tmp_path)['pocket-minha_voz']
    assert new_voice.model == target
    assert list(engine.stream('Arandu.', new_voice))[0].rate == 24000


def test_custom_voice_discovery_and_ignored_symlink(tmp_path):
    pocket = tmp_path / 'pocket'
    pocket.mkdir()
    (pocket / 'jaime.safetensors').write_bytes(b'x')
    (pocket / 'other.safetensors').symlink_to(pocket / 'jaime.safetensors')
    voices = catalogue(tmp_path)
    assert 'pocket-jaime' in voices
    assert 'pocket-other' not in voices
    assert not catalogue(tmp_path, pocket_enabled=False)
