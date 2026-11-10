"""Isolated adapter tests; no model download is needed."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "arandu_stack/rootfs/modules/arandu_stt/rootfs/src/arandu_stt/whisper_backend.py"
spec = importlib.util.spec_from_file_location("whisper_backend", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_requires_complete_local_model(tmp_path):
    with pytest.raises(FileNotFoundError):
        module.WhisperBackend(tmp_path).load()


def test_cpu_configuration_and_consumption(tmp_path, monkeypatch):
    for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"):
        (tmp_path / name).touch()
    consumed = []
    def segments():
        consumed.append(True)
        yield SimpleNamespace(text=" Liga ")
        yield SimpleNamespace(text=" a luz ")
    model = SimpleNamespace(transcribe=Mock(return_value=(segments(), None)))
    factory = Mock(return_value=model)
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=factory))
    backend = module.WhisperBackend(tmp_path, 2).load()
    text, elapsed = backend.recognize([0.0])
    assert text == "Liga a luz" and consumed and elapsed >= 0
    assert factory.call_args.kwargs == {"device": "cpu", "compute_type": "int8", "cpu_threads": 2, "num_workers": 1, "local_files_only": True}
    assert model.transcribe.call_args.kwargs["language"] == "pt"
    backend.close()
    with pytest.raises(RuntimeError):
        backend.recognize([0.0])


def test_rejects_threads_outside_range(tmp_path):
    with pytest.raises(ValueError):
        module.WhisperBackend(tmp_path, 5)
