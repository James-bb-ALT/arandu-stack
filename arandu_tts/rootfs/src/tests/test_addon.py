import os
import json
from pathlib import Path
from arandu_tts.addon_main import configure, read_options
from arandu_tts.config import Config


def test_options_translate(monkeypatch, tmp_path):
    path = tmp_path / "options.json"
    path.write_text(json.dumps({"voice": "pt_BR-test-medium", "cpu_threads": 1, "auto_download": False,
                                "remote_url": "http://10.0.0.1:10312", "remote_token": "valid-token"}))
    options = read_options(path)
    configure(options)
    cfg = Config.from_env()
    assert cfg.default_voice == "pt_BR-test-medium"
    assert cfg.piper_threads == 1
    assert not cfg.auto_download
    assert cfg.remote_url == "http://10.0.0.1:10312"
    assert cfg.remote_token == "valid-token"
    assert cfg.voices_dir == Path("/share/arandu_tts/voices")


def test_manifest_yaml():
    import yaml
    cfg = yaml.safe_load(Path("addon/config.yaml").read_text(encoding="utf-8"))
    assert cfg["slug"] == "arandu_tts"
    assert cfg["ports"]["10212/tcp"] == 10212
    assert "amd64" in cfg["arch"]
