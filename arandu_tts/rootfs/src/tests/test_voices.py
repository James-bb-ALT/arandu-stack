import json
from pathlib import Path

import pytest
from arandu_tts.voices import catalogue, assert_model_pair, valid_voice_id


def make_pair(path: Path, name="pt_BR-test-medium"):
    (path / f"{name}.onnx").write_bytes(b"dummy")
    (path / f"{name}.onnx.json").write_text(json.dumps({"language": {"code": "pt_BR"}}), encoding="utf-8")


def test_piper_pair_and_remote_catalog(tmp_path):
    make_pair(tmp_path)
    (tmp_path / "bad.onnx").write_bytes(b"dummy")
    (tmp_path / "remote_voices.json").write_text(json.dumps({"clone1": {"remote_voice": "minha_voz", "language": "pt_BR"}}))
    voices = catalogue(tmp_path)
    assert set(voices) == {"pt_BR-test-medium", "clone1", "pocket-rafael"}
    assert voices["pocket-rafael"].backend == "pocket"
    assert voices["pt_BR-test-medium"].backend == "piper"
    assert voices["clone1"].remote_voice == "minha_voz"
    assert assert_model_pair(tmp_path, "pt_BR-test-medium") == tmp_path / "pt_BR-test-medium.onnx"


def test_reject_path_traversal(tmp_path):
    assert not valid_voice_id("../secret")
    assert not valid_voice_id("a/b")
    assert not valid_voice_id("x .pt")
    assert valid_voice_id("pt_BR-arandu-medium")
    with pytest.raises(ValueError):
        assert_model_pair(tmp_path, "../../passwd")


def test_reject_bad_remote(tmp_path):
    (tmp_path / "remote_voices.json").write_text('{"danger": {"remote_voice": "../a"}}')
    with pytest.raises(ValueError):
        catalogue(tmp_path)


def test_reject_duplicate(tmp_path):
    make_pair(tmp_path, "arandu")
    (tmp_path / "remote_voices.json").write_text('{"arandu": {"remote_voice": "clone"}}')
    with pytest.raises(ValueError):
        catalogue(tmp_path)


def test_ignores_symlink_model(tmp_path):
    make_pair(tmp_path, "valid")
    (tmp_path / "external.onnx").symlink_to(tmp_path / "valid.onnx")
    (tmp_path / "external.onnx.json").write_text('{"language": {"code": "pt_BR"}}')
    assert "external" not in catalogue(tmp_path)
