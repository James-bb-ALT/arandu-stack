"""CPU Whisper inference adapter; model files must already be installed locally."""
from __future__ import annotations

from pathlib import Path
import threading
import time


class WhisperBackend:
    """Load a single local CTranslate2 model and serialize inference."""

    def __init__(self, model_dir: Path, threads: int = 2):
        if type(threads) is not int or not 1 <= threads <= 4:
            raise ValueError("threads must be between 1 and 4")
        self.model_dir = Path(model_dir)
        self.threads = threads
        self._model = None
        self._lock = threading.Lock()
        self.load_ms = None

    def load(self):
        if self._model is not None:
            return self
        required = ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt")
        missing = [name for name in required if not (self.model_dir / name).is_file()]
        if missing:
            raise FileNotFoundError("Incomplete Whisper model: " + ", ".join(missing))
        from faster_whisper import WhisperModel
        started = time.perf_counter()
        self._model = WhisperModel(str(self.model_dir), device="cpu", compute_type="int8",
                                   cpu_threads=self.threads, num_workers=1, local_files_only=True)
        self.load_ms = (time.perf_counter() - started) * 1000
        return self

    def recognize(self, audio):
        with self._lock:
            if self._model is None:
                raise RuntimeError("Whisper model is not loaded")
            start = time.perf_counter()
            segments, _ = self._model.transcribe(
                audio, language="pt", task="transcribe", beam_size=1, best_of=1,
                temperature=0, condition_on_previous_text=False,
                word_timestamps=False, vad_filter=False,
            )
            text = " ".join(segment.text.strip() for segment in segments).strip()
            return text, (time.perf_counter() - start) * 1000

    def close(self):
        with self._lock:
            self._model = None
