"""Teste opt-in do motor REAL via TCP. Sem hardware nem Home Assistant."""

import asyncio
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import wave
from pathlib import Path

from arandu_wake.probe import probe

SAMPLE_URL = "https://raw.githubusercontent.com/rhasspy/wyoming-openwakeword/456fdcc4c4e2205f629f48127b49b7acf845666c/tests/alexa.wav"
SAMPLE_SHA256 = "ef63936b116a808d1023d57c41d19846c798e81ab1fc3c3daffcfa442afdb28f"


@unittest.skipUnless(os.environ.get("ARANDU_TEST_DETECTOR") == "1", "Defina ARANDU_TEST_DETECTOR=1 e instale requirements-detector.txt")
class DetectorIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="arandu-wake-test-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.positive = cls.root / "alexa.wav"
        with urllib.request.urlopen(SAMPLE_URL, timeout=45) as response:
            data = response.read(2_000_000)
        if hashlib.sha256(data).hexdigest() != SAMPLE_SHA256:
            raise ValueError("Checksum da amostra pública mudou")
        cls.positive.write_bytes(data)
        cls.negative = cls.root / "silence.wav"
        with wave.open(str(cls.negative), "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            audio.writeframes(bytes(16000 * 2 * 3))
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            cls.port = sock.getsockname()[1]
        cls.uri = f"tcp://127.0.0.1:{cls.port}"
        cls.process = subprocess.Popen([sys.executable, "-m", "wyoming_openwakeword", "--uri", cls.uri], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        cls.addClassCleanup(cls.stop)
        for _ in range(100):
            if cls.process.poll() is not None:
                output, _ = cls.process.communicate()
                raise RuntimeError("Motor encerrou: " + output)
            try:
                with socket.create_connection(("127.0.0.1", cls.port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise TimeoutError("Motor não abriu porta local")

    @classmethod
    def stop(cls):
        cls.process.terminate()
        try:
            cls.process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            cls.process.kill()
            cls.process.communicate(timeout=5)

    def test_alexa_detection_over_wyoming(self):
        result = asyncio.run(probe(self.uri, str(self.positive), timeout=20))
        self.assertTrue(result["alexa_installed"])
        self.assertTrue(result["detected"], result)
        self.assertEqual(result["name"], "alexa")
        print("DETECTOR_POSITIVE " + json.dumps(result))

    def test_silence_is_not_detection(self):
        result = asyncio.run(probe(self.uri, str(self.negative), timeout=10, realtime=False))
        self.assertFalse(result["detected"])

    def test_reconnect_clients(self):
        for _ in range(3):
            self.assertTrue(asyncio.run(probe(self.uri))["alexa_installed"])

    def test_incompatible_wav_rejected(self):
        path = self.root / "bad-format.wav"
        with wave.open(str(path), "wb") as audio:
            audio.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
            audio.writeframes(bytes(4800 * 4))
        with self.assertRaisesRegex(ValueError, "PCM16 mono"):
            asyncio.run(probe(self.uri, str(path)))
