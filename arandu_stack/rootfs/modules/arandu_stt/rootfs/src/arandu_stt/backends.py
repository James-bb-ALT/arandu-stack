"""Common inference contract used by Wyoming and the native voice laboratory."""
from pathlib import Path
import threading
import time

from whisper_backend import WhisperBackend


class FastConformerBackend:
    def __init__(self, model_dir, threads=2):
        self.model_dir, self.threads = Path(model_dir), threads
        self._model = None
        self._lock = threading.Lock()

    def load(self):
        import sherpa_onnx
        model = self.model_dir / 'model.onnx'
        if not model.exists():
            model = self.model_dir / 'model.int8.onnx'
        tokens = self.model_dir / 'tokens.txt'
        if not model.is_file() or model.stat().st_size < 10_000_000 or not tokens.is_file():
            raise FileNotFoundError('Modelo ONNX incompleto')
        start = time.perf_counter()
        self._model = sherpa_onnx.OfflineRecognizer.from_nemo_ctc(
            model=str(model), tokens=str(tokens), num_threads=self.threads, sample_rate=16000,
            feature_dim=80, decoding_method='greedy_search', provider='cpu', debug=False)
        self.load_ms = (time.perf_counter() - start) * 1000
        return self

    def recognize(self, audio):
        with self._lock:
            start = time.perf_counter()
            stream = self._model.create_stream()
            stream.accept_waveform(16000, audio)
            self._model.decode_stream(stream)
            return stream.result.text.strip(), (time.perf_counter() - start) * 1000

    def close(self):
        self._model = None


def create_backend(model, directory, threads=2):
    from model_catalog import validate
    validate(model)
    if type(threads) is not int or not 1 <= threads <= 4:
        raise ValueError('Threads deve ficar entre 1 e 4')
    if model == 'fastconformer':
        factory = FastConformerBackend
    elif model.startswith('whisper_'):
        factory = WhisperBackend
    elif model.startswith('parakeet_'):
        from parakeet_backend import ParakeetBackend
        factory = ParakeetBackend
    elif model == 'orukeet_int8':
        from orukeet_backend import OrukeetBackend
        factory = OrukeetBackend
    else:
        from granite_backend import GraniteBackend
        factory = GraniteBackend
    return factory(directory, threads)
