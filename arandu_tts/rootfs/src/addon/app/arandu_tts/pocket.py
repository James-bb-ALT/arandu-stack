"""Motor Pocket TTS 3.3.0: CPU, português, vozes pré-processadas e chunks PCM16."""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Iterator

from .config import Config
from .voices import VoiceSpec

LOG = logging.getLogger(__name__)


class PocketEngine:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.lock = threading.RLock()
        self.model = None
        self.states: dict[str, tuple[int | None, object]] = {}

    @property
    def loaded(self) -> bool:
        return self.model is not None

    def load(self):
        if not self.cfg.pocket_enabled:
            raise RuntimeError('Pocket TTS desativado')
        with self.lock:
            if self.model is None:
                import torch
                from pocket_tts import TTSModel
                torch.set_num_threads(self.cfg.pocket_threads)
                LOG.info('Carregando Pocket TTS (CPU), idioma %s', self.cfg.pocket_language)
                self.model = TTSModel.load_model(language=self.cfg.pocket_language)
            return self.model

    def _state(self, voice: VoiceSpec):
        if voice.backend != 'pocket':
            raise ValueError('Voz não pertence ao Pocket TTS')
        model = self.load()
        if voice.model:
            if not voice.model.is_file() or voice.model.is_symlink():
                raise FileNotFoundError('Perfil da voz Pocket ausente')
            revision = voice.model.stat().st_mtime_ns
            source = str(voice.model)
        else:
            revision = None
            source = 'rafael'
        cached = self.states.get(voice.name)
        if cached is None or cached[0] != revision:
            LOG.info('Carregando perfil Pocket: %s', voice.name)
            self.states[voice.name] = (revision, model.get_state_for_audio_prompt(source))
        return self.states[voice.name][1]

    def drop_voice(self, name: str) -> None:
        # Exclui também o estado em memória; gravações permanecem privadas.
        with self.lock:
            self.states.pop(name, None)

    def prepare_voice(self, wav: Path, target: Path) -> None:
        """Clona amostra (consentida), exporta estado reutilizável de inferência."""
        if not wav.is_file() or wav.is_symlink():
            raise ValueError('Amostra inválida')
        if target.suffix != '.safetensors' or target.is_symlink():
            raise ValueError('Caminho do perfil inválido')
        with self.lock:
            model = self.load()
            from pocket_tts import export_model_state
            state = model.get_state_for_audio_prompt(str(wav), truncate=True)
            tmp = target.with_suffix('.pending.safetensors')
            try:
                export_model_state(state, tmp)
                os.replace(tmp, target)
            finally:
                tmp.unlink(missing_ok=True)
            name = f'pocket-{target.stem}'
            self.states[name] = (target.stat().st_mtime_ns, state)

    def stream(self, text: str, voice: VoiceSpec) -> Iterator['PCMChunk']:
        from .engine import PCMChunk
        with self.lock:
            model = self.load()
            state = self._state(voice)
            import numpy as np
            for tensor in model.generate_audio_stream(state, text):
                samples = np.asarray(tensor.detach().cpu().numpy(), dtype=np.float32).reshape(-1)
                if samples.size:
                    samples = np.nan_to_num(samples, nan=0.0, posinf=1.0, neginf=-1.0)
                    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype('<i2').tobytes()
                    yield PCMChunk(pcm, int(model.sample_rate), 2, 1)
