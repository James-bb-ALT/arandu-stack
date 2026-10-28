import copy
import json
import unittest
from pathlib import Path

from arandu_wake.config import validate_config, validate_url

ROOT = Path(__file__).resolve().parents[1]


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / "config/satellites.example.json").read_text())

    def test_example(self):
        self.assertEqual(validate_config(self.config), [])

    def test_duplicate_satellite(self):
        self.config["satellites"].append(copy.deepcopy(self.config["satellites"][0]))
        self.assertTrue(any("duplicado" in e for e in validate_config(self.config)))

    def test_single_pipeline(self):
        self.config["satellites"][0]["pipeline"] = "Outro"
        self.assertTrue(any("pipeline comum" in e for e in validate_config(self.config)))

    def test_threshold_rejects_nan_bool_and_out_of_range(self):
        for value in (float("nan"), float("inf"), True, -0.1, 1.1):
            with self.subTest(value=value):
                self.config["satellites"][0]["sensitivity"] = value
                self.assertTrue(validate_config(self.config))

    def test_only_alexa_for_mvp(self):
        self.config["wake_word"] = "arandu"
        self.assertTrue(validate_config(self.config))

    def test_missing_ha_is_reported(self):
        del self.config["home_assistant"]
        self.assertTrue(validate_config(self.config))

    def test_malformed_satellite(self):
        self.config["satellites"].append(None)
        self.assertTrue(validate_config(self.config))

    def test_url_rejects_embedded_credentials(self):
        for url in ("http://user:secret@localhost:8123", "https://example.com?token=x", "file:///x", "http://example.com", "https://example.com/subpath"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_url(url)

    def test_local_urls(self):
        for url in ("http://homeassistant.local:8123/", "http://192.168.1.2:8123", "http://[::1]:8123", "https://ha.example.com"):
            self.assertTrue(validate_url(url))
