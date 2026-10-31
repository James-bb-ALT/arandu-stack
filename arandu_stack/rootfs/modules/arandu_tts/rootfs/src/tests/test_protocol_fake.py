"""Contrato Wyoming testado com transportes fake. Não substitui teste real de rede."""
from __future__ import annotations

import asyncio
import json
import sys
import types

import pytest

from arandu_tts.config import Config
from arandu_tts.engine import PCMChunk
from arandu_tts import server as arandu_server


class E:
    def __init__(self, type, **data):
        self.type = type
        self.data = data


class EventBase:
    ev_type = ""
    @classmethod
    def is_type(cls, typ):
        return typ == cls.ev_type
    @classmethod
    def from_event(cls, event):
        return types.SimpleNamespace(**event.data)
    def __init__(self, **kw):
        self.values = kw
    def event(self):
        return E(self.ev_type, **self.values)


class FakeServer:
    instance = None
    def __init__(self):
        self.factory = None
        FakeServer.instance = self
    @classmethod
    def from_uri(cls, uri):
        assert uri == "tcp://0.0.0.0:10212"
        return cls()
    async def run(self, factory):
        self.factory = factory


class FakeHandler:
    def __init__(self, reader, writer):
        self.sent = []
    async def write_event(self, event):
        self.sent.append(event)


def mock_wyoming(monkeypatch):
    components = {
        "wyoming.audio": [("AudioChunk", "audio-chunk"), ("AudioStart", "audio-start"), ("AudioStop", "audio-stop")],
        "wyoming.error": [("Error", "error")],
        "wyoming.info": [("Describe", "describe"), ("Info", "info")],
        "wyoming.tts": [("Synthesize", "synthesize"), ("SynthesizeStart", "synthesize-start"),
                        ("SynthesizeChunk", "synthesize-chunk"), ("SynthesizeStop", "synthesize-stop"),
                        ("SynthesizeStopped", "synthesize-stopped")],
    }
    for module_name, classes in components.items():
        module = types.ModuleType(module_name)
        for class_name, ev_type in classes:
            typ = type(class_name, (EventBase,), {"ev_type": ev_type})
            setattr(module, class_name, typ)
        if module_name == "wyoming.info":
            for name in ("Attribution", "TtsProgram", "TtsVoice"):
                setattr(module, name, type(name, (EventBase,), {"ev_type": "unused"}))
        monkeypatch.setitem(sys.modules, module_name, module)
    servermod = types.ModuleType("wyoming.server")
    servermod.AsyncEventHandler = FakeHandler
    servermod.AsyncServer = FakeServer
    monkeypatch.setitem(sys.modules, "wyoming.server", servermod)


@pytest.mark.asyncio
async def test_wyoming_one_shot_and_description(monkeypatch, tmp_path):
    mock_wyoming(monkeypatch)
    (tmp_path / "test.onnx").write_bytes(b"dummy")
    (tmp_path / "test.onnx.json").write_text(json.dumps({"language": {"code": "pt_BR"}}))
    monkeypatch.setattr(arandu_server, "ensure_default_model", lambda _: None)
    class FakeEngine:
        def __init__(self, cfg):
            pass
        async def stream(self, text, voice):
            assert text == "Luz acesa."
            assert voice.name == "test"
            yield PCMChunk(b"\x00\x00" * 160, 22050, 2, 1)
    monkeypatch.setattr(arandu_server, "SpeechEngine", FakeEngine)
    cfg = Config(voices_dir=tmp_path, default_voice="test", preload=False, auto_download=False)
    await arandu_server.serve(cfg)
    handler = FakeServer.instance.factory(None, None)
    await handler.handle_event(E("describe"))
    await handler.handle_event(E("synthesize", text="Luz acesa.", voice=None))
    assert [e.type for e in handler.sent] == ["info", "audio-start", "audio-chunk", "audio-stop"]
    info = handler.sent[0].data["tts"][0]
    assert info.values["voices"][0].values["name"] == "test"
    # HA uses this capability to select its complete-audio path. Advertising
    # streaming lets slow synthesis drain the satellite buffer mid-utterance.
    assert info.values["supports_synthesize_streaming"] is False


@pytest.mark.asyncio
async def test_wyoming_streaming_text(monkeypatch, tmp_path):
    mock_wyoming(monkeypatch)
    (tmp_path / "test.onnx").write_bytes(b"dummy")
    (tmp_path / "test.onnx.json").write_text('{"language": {"code": "pt_BR"}}')
    monkeypatch.setattr(arandu_server, "ensure_default_model", lambda _: None)
    class FakeEngine:
        def __init__(self, cfg):
            pass
        async def stream(self, text, voice):
            yield PCMChunk(b"\x00\x00" * 32, 22050, 2, 1)
    monkeypatch.setattr(arandu_server, "SpeechEngine", FakeEngine)
    await arandu_server.serve(Config(voices_dir=tmp_path, default_voice="test", preload=False))
    handler = FakeServer.instance.factory(None, None)
    await handler.handle_event(E("synthesize-start", voice=None))
    await handler.handle_event(E("synthesize-chunk", text="Luz acesa. Outra"))
    await handler.handle_event(E("synthesize-chunk", text=" frase aqui."))
    await handler.handle_event(E("synthesize-stop"))
    assert [event.type for event in handler.sent] == [
        "audio-start", "audio-chunk", "audio-chunk", "audio-stop", "synthesize-stopped"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize('text', ['Luz acesa.', 'Luz acesa sem pontuação'])
async def test_ha_streaming_compatibility_message_is_not_spoken_twice(monkeypatch, tmp_path, text):
    mock_wyoming(monkeypatch)
    (tmp_path / 'test.onnx').write_bytes(b'dummy')
    (tmp_path / 'test.onnx.json').write_text('{"language": {"code": "pt_BR"}}')
    monkeypatch.setattr(arandu_server, 'ensure_default_model', lambda _: None)
    spoken = []
    class FakeEngine:
        def __init__(self, cfg):
            pass
        async def stream(self, text, voice):
            spoken.append(text)
            yield PCMChunk(b'\x00\x00' * 32, 22050, 2, 1)
    monkeypatch.setattr(arandu_server, 'SpeechEngine', FakeEngine)
    await arandu_server.serve(Config(voices_dir=tmp_path, default_voice='test', preload=False))
    handler = FakeServer.instance.factory(None, None)
    for event in [E('synthesize-start', voice=None), E('synthesize-chunk', text=text),
                  E('synthesize', text=text, voice=None), E('synthesize-stop')]:
        assert await handler.handle_event(event)
    assert spoken == [text]
    assert [event.type for event in handler.sent] == ['audio-start', 'audio-chunk', 'audio-stop', 'synthesize-stopped']
    # A later independent request for the same words must still be spoken.
    assert await handler.handle_event(E('synthesize', text=text, voice=None))
    assert spoken == [text, text]

@pytest.mark.asyncio
async def test_remote_gpu_failure_falls_back_to_piper(monkeypatch, tmp_path):
    mock_wyoming(monkeypatch)
    (tmp_path / "pt_BR-faber-medium.onnx").write_bytes(b"dummy")
    (tmp_path / "pt_BR-faber-medium.onnx.json").write_text('{"language": {"code": "pt_BR"}}')
    (tmp_path / "remote_voices.json").write_text('{"custom": {"remote_voice": "voz1", "language": "pt_BR"}}')
    monkeypatch.setattr(arandu_server, "ensure_default_model", lambda _: None)
    called = []
    class FakeEngine:
        def __init__(self, cfg):
            pass
        async def stream(self, text, voice):
            called.append(voice.name)
            if voice.backend == "remote":
                raise ConnectionError("desktop offline")
            yield PCMChunk(b"\x01\x00" * 100, 22050, 2, 1)
    monkeypatch.setattr(arandu_server, "SpeechEngine", FakeEngine)
    await arandu_server.serve(Config(voices_dir=tmp_path, default_voice="custom", preload=False))
    handler = FakeServer.instance.factory(None, None)
    await handler.handle_event(E("synthesize", text="Teste de voz.", voice=None))
    assert called == ["custom", "pt_BR-faber-medium"]
    assert [e.type for e in handler.sent] == ["audio-start", "audio-chunk", "audio-stop"]
