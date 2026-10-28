import asyncio
import io
import json
import sys
import types
import wave
from dataclasses import dataclass

import pytest

from arandu_tts.config import Config
from arandu_tts.engine import SpeechEngine, parse_wav, split_pcm
from arandu_tts.voices import VoiceSpec


def wav_bytes(samples=b"\x00\x00" * 100, nchannels=1):
    with io.BytesIO() as buffer:
        with wave.open(buffer, "wb") as out:
            out.setnchannels(nchannels)
            out.setsampwidth(2)
            out.setframerate(24000)
            out.writeframes(samples)
        return buffer.getvalue()


def test_wav_audio_chunks():
    content = wav_bytes(b"\x00\x00" * 5000)
    result = list(parse_wav(content))
    assert len(result) == 3
    assert sum(len(ch.data) for ch in result) == 10000
    assert all(ch.rate == 24000 for ch in result)


def test_reject_wav_non_mono():
    with pytest.raises(ValueError):
        list(parse_wav(wav_bytes(nchannels=2)))


def test_alignment():
    with pytest.raises(ValueError):
        list(split_pcm(b"\x01\x00\x01", 22050, 2, 1))


@pytest.mark.asyncio
async def test_fake_piper_stream(monkeypatch, tmp_path):
    calls = []

    class FakePiper:
        @staticmethod
        def load(model, use_cuda=False):
            calls.append((model, use_cuda))
            return FakePiper()

        def synthesize(self, text):
            assert text == "Luz acesa."
            yield types.SimpleNamespace(audio_int16_bytes=b"\x01\x00" * 3000,
                                        sample_rate=22050, sample_width=2, sample_channels=1)

    monkeypatch.setitem(sys.modules, "piper", types.SimpleNamespace(PiperVoice=FakePiper))
    spec = VoiceSpec("test", "pt_BR", "piper", tmp_path / "test.onnx")
    engine = SpeechEngine(Config(voices_dir=tmp_path))
    chunks = [chunk async for chunk in engine.stream("Luz acesa.", spec)]
    assert len(chunks) == 2
    assert sum(len(ch.data) for ch in chunks) == 6000
    assert calls == [(spec.model, False)]
    chunks2 = [chunk async for chunk in engine.stream("Luz acesa.", spec)]
    assert len(calls) == 1  # cache em RAM
    assert chunks2 == chunks


@pytest.mark.asyncio
async def test_fake_engine_error_propagates(tmp_path):
    engine = SpeechEngine(Config(voices_dir=tmp_path))
    async def collect():
        return [c async for c in engine.stream("hello", VoiceSpec("bad", "pt_BR", "nonsense"))]
    with pytest.raises(ValueError, match="Backend desconhecido"):
        await collect()


def test_remote_requires_token(tmp_path):
    engine = SpeechEngine(Config(voices_dir=tmp_path, remote_url="http://127.0.0.1:1"))
    spec = VoiceSpec("clone", "pt_BR", "remote", remote_voice="clone")
    with pytest.raises(RuntimeError, match="TOKEN"):
        list(engine.stream_sync("Oi", spec))
