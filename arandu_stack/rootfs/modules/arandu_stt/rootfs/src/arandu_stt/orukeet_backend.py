"""Orukeet uses sherpa's transducer API, independently of the CTC backend."""
from pathlib import Path
import threading
import time


class OrukeetBackend:
    def __init__(self, model_dir, threads=2):
        self.model_dir, self.threads = Path(model_dir), threads
        self._model = None
        self._lock = threading.Lock()

    def load(self):
        import sherpa_onnx
        start = time.perf_counter()
        self._model = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(self.model_dir/'encoder.int8.onnx'), decoder=str(self.model_dir/'decoder.int8.onnx'),
            joiner=str(self.model_dir/'joiner.int8.onnx'), tokens=str(self.model_dir/'tokens.txt'),
            model_type='nemo_transducer', sample_rate=16000, feature_dim=128,
            decoding_method='greedy_search', provider='cpu', num_threads=self.threads)
        self.load_ms = (time.perf_counter()-start)*1000
        return self

    def recognize(self, audio):
        with self._lock:
            start = time.perf_counter()
            stream = self._model.create_stream()
            stream.accept_waveform(16000, audio)
            self._model.decode_stream(stream)
            return stream.result.text.strip(), (time.perf_counter()-start)*1000

    def close(self):
        with self._lock:
            self._model = None
