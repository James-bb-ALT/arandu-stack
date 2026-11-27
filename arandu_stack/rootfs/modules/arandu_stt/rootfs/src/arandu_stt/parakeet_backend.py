"""Local-only onnx-asr TDT; shared by both Portuguese exports."""
from pathlib import Path
import threading
import time


class ParakeetBackend:
    def __init__(self, model_dir, threads=2):
        self.model_dir, self.threads = Path(model_dir), threads
        self._model = None
        self._lock = threading.Lock()

    def load(self):
        import onnxruntime as ort
        from onnx_asr.loader import Manager
        # Explicit offline resolution also prevents missing files triggering Hub downloads.
        options = ort.SessionOptions()
        options.intra_op_num_threads = self.threads
        options.inter_op_num_threads = 1
        options.enable_cpu_mem_arena = False
        # Keep FP32 external weights memory-safe; INT8 uses the same graph setup.
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
        start = time.perf_counter()
        providers = ['CPUExecutionProvider']
        available = set(ort.get_available_providers()) if hasattr(ort, 'get_available_providers') else {'CPUExecutionProvider'}
        if 'CUDAExecutionProvider' in available and getattr(self, 'prefer_cuda', False):
            providers.insert(0, 'CUDAExecutionProvider')
        manager = Manager(sess_options=options, providers=providers,
                          preprocessor_config={'use_numpy_preprocessors': True, 'max_concurrent_workers': 1})
        quantization = 'int8' if self.model_dir.name.endswith('_int8') else None
        self._model = manager.create_asr('nemo-conformer-tdt', self.model_dir, offline=True,
                                         **({'quantization': quantization} if quantization else {}))
        self.providers = providers
        self.load_ms = (time.perf_counter()-start)*1000
        return self

    def recognize(self, audio):
        with self._lock:
            start = time.perf_counter()
            # TDT is not language-prompted; onnx-asr's language option is for Whisper/Canary.
            text = self._model.recognize(audio, sample_rate=16000)
            return text.strip(), (time.perf_counter()-start)*1000

    def close(self):
        with self._lock:
            self._model = None
