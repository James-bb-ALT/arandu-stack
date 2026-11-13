"""Persistent in-memory Phonon 2.5 backend using fermion-research."""
from __future__ import annotations

from pathlib import Path
import threading
import time


class PhononBackend:
    def __init__(self, model_dir, threads=2):
        self.model_dir = Path(model_dir)
        self.threads = int(threads)
        self._model = None
        self._lock = threading.Lock()
        self.load_ms = 0.0

    def load(self):
        weights = self.model_dir / 'phonon-2.5.fsq'
        if not weights.is_file() or weights.stat().st_size < 10_000_000:
            raise FileNotFoundError('Pesos phonon-2.5.fsq ausentes ou incompletos')
        import fermion_research  # type: ignore

        start = time.perf_counter()
        if hasattr(fermion_research, 'load_model'):
            self._model = fermion_research.load_model(str(weights), threads=self.threads)
        elif hasattr(fermion_research, 'Phonon'):
            self._model = fermion_research.Phonon(str(weights), threads=self.threads)
        else:
            raise ImportError('API Python do fermion-research incompatível')
        self.load_ms = (time.perf_counter() - start) * 1000.0
        return self

    def recognize(self, audio):
        if self._model is None:
            raise RuntimeError('Phonon 2.5 não carregado')
        with self._lock:
            start = time.perf_counter()
            if hasattr(self._model, 'transcribe_array'):
                out = self._model.transcribe_array(audio, sample_rate=16000)
            elif hasattr(self._model, 'transcribe'):
                out = self._model.transcribe(audio, sample_rate=16000)
            else:
                raise RuntimeError('Método transcribe ausente no runtime Phonon 2.5')
            text = out.get('text', '') if isinstance(out, dict) else (getattr(out, 'text', None) or str(out or ''))
            return text.strip(), (time.perf_counter() - start) * 1000.0

    def close(self):
        with self._lock:
            if self._model is not None and hasattr(self._model, 'close'):
                try:
                    self._model.close()
                except Exception:
                    pass
            self._model = None
