"""Persistent transcribe.cpp backend for Parakeet Ultra (Q8_0 default, Q4_K_M variant)."""
from __future__ import annotations

from pathlib import Path
import threading
import time

PARAKEET_ULTRA_FILES = {
    'parakeet_ultra_q8': 'parakeet-ultra-Q8_0.gguf',
    'parakeet_ultra_q4': 'parakeet-ultra-Q4_K_M.gguf',
}


class ParakeetUltraBackend:
    def __init__(self, model_dir, threads=2, model_id='parakeet_ultra_q8'):
        self.model_dir = Path(model_dir)
        self.threads = int(threads)
        self.model_id = model_id
        self.filename = PARAKEET_ULTRA_FILES.get(model_id, 'parakeet-ultra-Q8_0.gguf')
        self._model = None
        self._session = None
        self._lock = threading.Lock()
        self.load_ms = 0.0

    def load(self):
        gguf = self.model_dir / self.filename
        if not gguf.is_file() or gguf.stat().st_size < 10_000_000:
            raise FileNotFoundError(f'Arquivo GGUF ausente ou incompleto: {gguf.name}')
        import transcribe_cpp

        start = time.perf_counter()
        try:
            self._model = transcribe_cpp.Model(str(gguf), backend='cpu')
            langs = getattr(getattr(self._model, 'capabilities', None), 'languages', None)
            if langs is not None and 'pt' not in langs:
                raise ValueError('Parakeet Ultra GGUF sem suporte a português')
            self._session = self._model.session(n_threads=self.threads)
        except BaseException:
            self.close()
            raise
        self.load_ms = (time.perf_counter() - start) * 1000.0
        return self

    def recognize(self, audio):
        if self._session is None:
            raise RuntimeError('Parakeet Ultra não carregado')
        with self._lock:
            start = time.perf_counter()
            result = self._session.run(audio, language='pt', timestamps='none')
            text = (getattr(result, 'text', None) or str(result or '')).strip()
            return text, (time.perf_counter() - start) * 1000.0

    def close(self):
        with self._lock:
            if self._session is not None:
                try:
                    self._session.close()
                except Exception:
                    pass
                self._session = None
            if self._model is not None:
                try:
                    self._model.close()
                except Exception:
                    pass
                self._model = None
