import asyncio
import sys
from pathlib import Path
from unittest import IsolatedAsyncioTestCase

import numpy as np
from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.info import Describe, Info

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from server import Handler, NAME


class FakeEngine:
    def recognize(self, audio):
        return "liga teve da sala", 0.01, 0.0


class FakeResolver:
    def apply(self, text, profile):
        return {"text": "liga TV da sala", "correction_ms": 1.0, "changes": ["TV"]}


class FakeMetrics:
    def __init__(self):
        self.entries = []

    def log(self, **entry):
        self.entries.append(entry)


class TestHandler(Handler):
    def __init__(self):
        self.events = []
        self.metrics_obj = FakeMetrics()
        super().__init__(FakeEngine(), FakeResolver(), self.metrics_obj, 0, "full", None, None)

    async def write_event(self, event):
        self.events.append(event)


class TestWyomingProtocol(IsolatedAsyncioTestCase):
    async def test_describe_transcribe_audio_transcript(self):
        handler = TestHandler()
        self.assertTrue(await handler.handle_event(Describe().event()))
        self.assertIsNotNone(Info.from_event(handler.events[-1]))

        self.assertTrue(await handler.handle_event(Transcribe().event()))
        self.assertTrue(await handler.handle_event(AudioStart(rate=16000, width=2, channels=1).event()))
        pcm = (np.zeros(16000, dtype="<i2")).tobytes()
        self.assertTrue(await handler.handle_event(AudioChunk(rate=16000, width=2, channels=1, audio=pcm).event()))
        self.assertFalse(await handler.handle_event(AudioStop().event()))

        transcript = Transcript.from_event(handler.events[-1])
        self.assertEqual(transcript.text, "liga TV da sala")
        self.assertEqual(handler.metrics_obj.entries[-1]["outcome"], "ok")

    async def test_unknown_model_returns_empty_transcript(self):
        handler = TestHandler()
        self.assertTrue(await handler.handle_event(Transcribe(name="outro-modelo").event()))
        self.assertTrue(await handler.handle_event(AudioStart(rate=16000, width=2, channels=1).event()))
        pcm = (np.zeros(16000, dtype="<i2")).tobytes()
        self.assertTrue(await handler.handle_event(AudioChunk(rate=16000, width=2, channels=1, audio=pcm).event()))
        self.assertFalse(await handler.handle_event(AudioStop().event()))
        transcript = Transcript.from_event(handler.events[-1])
        self.assertEqual(transcript.text, "")
        self.assertEqual(handler.metrics_obj.entries[-1]["outcome"], "invalid")

    async def test_stereo_audio_is_converted(self):
        handler = TestHandler()
        self.assertTrue(await handler.handle_event(Transcribe(name=NAME).event()))
        self.assertTrue(await handler.handle_event(AudioStart(rate=48000, width=2, channels=2).event()))
        pcm = (np.zeros(48000 * 2, dtype="<i2")).tobytes()
        self.assertTrue(await handler.handle_event(AudioChunk(rate=48000, width=2, channels=2, audio=pcm).event()))
        self.assertFalse(await handler.handle_event(AudioStop().event()))
        self.assertEqual(handler.metrics_obj.entries[-1]["outcome"], "ok")

