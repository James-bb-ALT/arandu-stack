"""Unit & integration tests for TTS RESIDENT / LAZY / OFF (Piper + Pocket) and Dashboard UI."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer

TTS_SRC = Path(__file__).resolve().parents[1] / "arandu_stack/rootfs/modules/arandu_tts/rootfs/src"
sys.path.insert(0, str(TTS_SRC))

from arandu_tts import engine as tts_engine  # noqa: E402
from arandu_tts import pocket as tts_pocket  # noqa: E402
from arandu_tts import web as tts_web  # noqa: E402
from arandu_tts.config import Config  # noqa: E402
from arandu_tts.voices import VoiceSpec  # noqa: E402
from arandu_stack.app.modules import DEFAULTS, ModuleManager, validate_options  # noqa: E402
from arandu_stack.app.settings import FIELDS  # noqa: E402


class _FakePiperModel:
    def __init__(self, name):
        self.name = name

    def synthesize(self, text):
        return []


class TestTtsResidencyAndMigration(unittest.IsolatedAsyncioTestCase):
    def test_defaults_settings_no_redundant_preload_and_legacy_migration(self):
        self.assertEqual(DEFAULTS["tts"]["resident_policy"], "RESIDENT")
        field_ids = [f["id"] for f in FIELDS["tts"]]
        self.assertIn("resident_policy", field_ids)
        self.assertNotIn("preload", field_ids)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Legacy Piper preload=False -> LAZY
            opts_lazy = validate_options("tts", {"voice": "pt_BR-arandu-medium", "preload": False}, root)
            self.assertEqual(opts_lazy["resident_policy"], "LAZY")
            # Legacy Pocket primary with pocket_preload=True -> RESIDENT
            opts_pkt = validate_options(
                "tts",
                {"voice": "pocket-rafael", "pocket_enabled": True, "pocket_preload": True},
                root,
            )
            self.assertEqual(opts_pkt["resident_policy"], "RESIDENT")
            # Roundtrip modules.json migration
            (root / "modules.json").write_text(
                json.dumps({"version": 2, "modules": {"tts": {"voice": "pt_BR-faber-medium", "preload": False}}}),
                encoding="utf-8",
            )
            mgr = ModuleManager(root, local=True)
            self.assertEqual(mgr.config["tts"]["resident_policy"], "LAZY")
            self.assertEqual(mgr.config["tts"]["voice"], "pt_BR-faber-medium")

    def test_piper_resident_pins_primary_across_temporary_secondary_voice(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(voices_dir=Path(tmp), default_voice="pt_BR-arandu-medium", resident_policy="RESIDENT")
            eng = tts_engine.SpeechEngine(cfg)
            primary = VoiceSpec(name="pt_BR-arandu-medium", backend="piper", language="pt_BR", description="Arandu", model=Path(tmp) / "a.onnx")
            secondary = VoiceSpec(name="pt_BR-faber-medium", backend="piper", language="pt_BR", description="Faber", model=Path(tmp) / "f.onnx")
            third = VoiceSpec(name="pt_BR-extra-medium", backend="piper", language="pt_BR", description="Extra", model=Path(tmp) / "e.onnx")
            with patch.object(tts_engine, "load_piper", side_effect=lambda v, t: _FakePiperModel(v.name)):
                eng.preload(primary)
                self.assertTrue(eng.is_primary_voice_loaded)
                eng._load(secondary)
                eng._load(third)
                # Primary must stay pinned in cache!
                self.assertTrue(eng.is_primary_voice_loaded)
                eng.close()
                self.assertFalse(eng.is_primary_voice_loaded)

    def test_pocket_residency_requires_model_and_voice_state_and_unloads_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(voices_dir=Path(tmp), default_voice="pocket-rafael", resident_policy="RESIDENT", pocket_enabled=True)
            pkt = tts_pocket.PocketEngine(cfg)
            voice = VoiceSpec(name="pocket-rafael", backend="pocket", language="pt_BR", description="Rafael", model=None)
            fake_model = SimpleNamespace(get_state_for_audio_prompt=lambda src: {"voice": src})
            with patch.object(pkt, "load", return_value=fake_model):
                pkt.model = fake_model
                # Model loaded alone without voice state is NOT ready for primary voice
                self.assertFalse(pkt.has_voice_loaded("pocket-rafael"))
                pkt.preload_voice(voice)
                self.assertTrue(pkt.has_voice_loaded("pocket-rafael"))
                pkt.unload()
                self.assertFalse(pkt.loaded)
                self.assertFalse(pkt.has_voice_loaded("pocket-rafael"))

    async def test_off_rejects_web_synthesize_and_reports_honest_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(
                voices_dir=Path(tmp),
                default_voice="pt_BR-arandu-medium",
                resident_policy="OFF",
                web_ingress_only=False,
                web_token="test-tok",
            )
            eng = tts_engine.SpeechEngine(cfg)
            app = tts_web.create_app(cfg, eng)
            server = TestServer(app)
            client = TestClient(server)
            await client.start_server()
            try:
                hdrs = {"X-Arandu-Token": "test-tok", "X-Arandu-UI": "1"}
                st = await (await client.get("/api/status", headers=hdrs)).json()
                self.assertEqual(st["resident_policy"], "OFF")
                self.assertFalse(st["primary_voice_loaded"])
                self.assertEqual(st["state"], "off")
                synth = await client.post("/api/synthesize", headers=hdrs, json={"text": "olá", "engine": "piper"})
                self.assertEqual(synth.status, 503)
                self.assertIn("tts_disabled", await synth.text())
            finally:
                await client.close()

    def test_dashboard_ui_contains_stt_and_tts_residency_panels(self):
        app_js = (Path(__file__).resolve().parents[1] / "arandu_stack/app/static/app.js").read_text(encoding="utf-8")
        self.assertIn("Aguardando primeira transcrição", app_js)
        self.assertIn("renderTtsManager", app_js)
        self.assertIn("tts/status", app_js)
        self.assertIn("Carregado realmente?:", app_js)


if __name__ == "__main__":
    unittest.main()
