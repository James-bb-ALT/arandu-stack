"""Unit & lifecycle tests for STT RESIDENT / LAZY / OFF policies."""
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import numpy as np

STT_SRC = Path(__file__).resolve().parents[1] / "arandu_stack/rootfs/modules/arandu_stt/rootfs/src/arandu_stt"
sys.path.insert(0, str(STT_SRC))

if "wyoming" not in sys.modules:
    for mod_name in ("wyoming", "wyoming.asr", "wyoming.audio", "wyoming.error", "wyoming.event", "wyoming.info", "wyoming.server"):
        m = ModuleType(mod_name)
        for attr in ("Transcribe", "Transcript", "AudioChunk", "AudioChunkConverter", "AudioStart", "AudioStop", "Error", "Event", "AsrModel", "AsrProgram", "Attribution", "Describe", "Info", "AsyncEventHandler", "AsyncServer"):
            setattr(m, attr, type(attr, (), {}))
        sys.modules[mod_name] = m
if "rapidfuzz" not in sys.modules:
    rf = ModuleType("rapidfuzz")
    rf.__path__ = []
    rf_fuzz = ModuleType("rapidfuzz.fuzz")
    rf_fuzz.ratio = lambda a, b: 0.0
    rf_dist = ModuleType("rapidfuzz.distance")
    rf_dist.Levenshtein = SimpleNamespace(distance=lambda a, b: 0)
    rf.fuzz = rf_fuzz
    rf.distance = rf_dist
    sys.modules["rapidfuzz"] = rf
    sys.modules["rapidfuzz.fuzz"] = rf_fuzz
    sys.modules["rapidfuzz.distance"] = rf_dist
if "websockets" not in sys.modules:
    sys.modules["websockets"] = ModuleType("websockets")

import backends  # noqa: E402
import server as stt_server  # noqa: E402
from arandu_stack.app.modules import DEFAULTS, validate_options  # noqa: E402
from arandu_stack.app.settings import FIELDS  # noqa: E402


class _StubBackend:
    instances = []

    def __init__(self, model_dir):
        self.model_dir = Path(model_dir)
        self.load_ms = 42.0
        self.load_calls = 0
        self.close_calls = 0
        self.recognize_calls = 0
        _StubBackend.instances.append(self)

    def load(self):
        self.load_calls += 1
        time.sleep(0.02)
        return self

    def recognize(self, audio):
        self.recognize_calls += 1
        return "luz ligada", 15.0

    def close(self):
        self.close_calls += 1


class TestSttResidency(unittest.TestCase):
    def setUp(self):
        _StubBackend.instances.clear()

    def test_defaults_settings_and_validation(self):
        self.assertEqual(DEFAULTS["stt"]["resident_policy"], "RESIDENT")
        f = next(x for x in FIELDS["stt"] if x["id"] == "resident_policy")
        self.assertEqual([c[0] for c in f["choices"]], ["RESIDENT", "LAZY", "OFF"])
        with tempfile.TemporaryDirectory() as tmp:
            for p in ("RESIDENT", "LAZY", "OFF"):
                self.assertEqual(validate_options("stt", {"resident_policy": p}, Path(tmp))["resident_policy"], p)
            with self.assertRaises(ValueError):
                validate_options("stt", {"resident_policy": "INVALID"}, Path(tmp))

    def test_resident_loads_once_reuses_and_closes_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(backends, "create_backend", side_effect=lambda m, d, t: _StubBackend(d)):
                eng = stt_server.Engine(Path(tmp), 2, "orukeet_int8", "adapted", resident_policy="RESIDENT")
                self.assertEqual(len(_StubBackend.instances), 1)
                self.assertEqual(_StubBackend.instances[0].load_calls, 1)
                meta = eng.metadata()
                self.assertTrue(meta["model_loaded"])
                self.assertEqual(meta["state"], "ready")
                self.assertEqual(meta["cold_start_count"], 1)

                audio = np.ones(16000, dtype=np.float32)
                eng.recognize(audio)
                eng.recognize(audio)
                self.assertEqual(_StubBackend.instances[0].load_calls, 1)
                self.assertEqual(_StubBackend.instances[0].recognize_calls, 2)

                eng.close()
                eng.close()  # idempotent
                self.assertEqual(_StubBackend.instances[0].close_calls, 1)
                self.assertFalse(eng.metadata()["model_loaded"])

    def test_lazy_waits_for_first_audio_and_serializes_concurrent_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(backends, "create_backend", side_effect=lambda m, d, t: _StubBackend(d)):
                eng = stt_server.Engine(Path(tmp), 2, "orukeet_int8", "adapted", resident_policy="LAZY")
                self.assertEqual(len(_StubBackend.instances), 0)
                meta0 = eng.metadata()
                self.assertFalse(meta0["model_loaded"])
                self.assertIsNone(meta0["active_model"])
                self.assertEqual(meta0["state"], "waiting_first_use")
                self.assertEqual(meta0["cold_start_count"], 0)

                # Two concurrent ensure_loaded() calls must only instantiate/load 1 backend
                barrier = threading.Barrier(2)
                results = []

                def worker():
                    barrier.wait()
                    results.append(eng.ensure_loaded())

                t1 = threading.Thread(target=worker)
                t2 = threading.Thread(target=worker)
                t1.start()
                t2.start()
                t1.join()
                t2.join()
                self.assertEqual(len(_StubBackend.instances), 1)
                self.assertEqual(_StubBackend.instances[0].load_calls, 1)
                self.assertIs(results[0], results[1])

                audio = np.ones(16000, dtype=np.float32)
                eng.recognize(audio)
                self.assertEqual(_StubBackend.instances[0].load_calls, 1)
                self.assertEqual(eng.metadata()["cold_start_count"], 1)

    def test_off_never_loads_and_raises_stt_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(backends, "create_backend", side_effect=lambda m, d, t: _StubBackend(d)):
                eng = stt_server.Engine(Path(tmp), 2, "orukeet_int8", "adapted", resident_policy="OFF")
                self.assertEqual(len(_StubBackend.instances), 0)
                meta = eng.metadata()
                self.assertEqual(meta["state"], "off")
                self.assertFalse(meta["model_loaded"])
                with self.assertRaises(stt_server.SttDisabledError) as ctx:
                    eng.recognize(np.ones(16000, dtype=np.float32))
                self.assertEqual(ctx.exception.code, "stt_disabled")
                self.assertEqual(len(_StubBackend.instances), 0)


if __name__ == "__main__":
    unittest.main()
