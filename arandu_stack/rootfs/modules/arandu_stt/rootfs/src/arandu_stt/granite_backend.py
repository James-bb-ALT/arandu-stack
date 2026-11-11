"""Persistent transcribe.cpp CPU model/session. No per-utterance CLI process."""
from pathlib import Path
import threading
import time


class GraniteBackend:
    def __init__(self, model_dir, threads=2):
        self.model_dir, self.threads = Path(model_dir), threads
        self._model = self._session = None
        self._lock = threading.Lock()

    def load(self):
        import transcribe_cpp
        start = time.perf_counter()
        try:
            self._model = transcribe_cpp.Model(str(self.model_dir/'granite-speech-5.0-470m-turboctc-Q4_K_M.gguf'), backend='cpu')
            if 'pt' not in self._model.capabilities.languages:
                raise ValueError('Granite GGUF sem suporte a português; conversão antiga rejeitada')
            self._session = self._model.session(n_threads=self.threads)
        except BaseException:
            self.close()
            raise
        self.load_ms = (time.perf_counter()-start)*1000
        return self

    def recognize(self, audio):
        with self._lock:
            start = time.perf_counter()
            result = self._session.run(audio, language='pt', timestamps='none')
            return result.text.strip(), (time.perf_counter()-start)*1000

    def close(self):
        with self._lock:
            if self._session is not None:
                self._session.close()
                self._session = None
            if self._model is not None:
                self._model.close()
                self._model = None
