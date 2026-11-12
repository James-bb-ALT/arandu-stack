"""Tests for safe baseline promotion."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import record


class RecordTests(unittest.TestCase):
    def make_source(self, directory: Path, scored_failures: int = 0, version: int = 2) -> None:
        suffix = f"v{version}"
        freeze_id = f"ptbr-independent-v{version}"
        cases = [] if not scored_failures else [{"bucket": "expected-pass-now", "classification": "semantic_mismatch"}]
        run = {"schema_version": 1, "freeze_id": freeze_id, "cases": cases, "hashes": {"binary": "b"}}
        benchmark = {"schema_version": 1, "freeze_id": freeze_id, "summary": {"failures": 0, "cold_failures": 0}, "hashes": {"binary": "b"}}
        (directory / f"run-{suffix}.json").write_text(json.dumps(run), encoding="utf-8")
        (directory / f"benchmark-{suffix}.json").write_text(json.dumps(benchmark), encoding="utf-8")
        for name in (f"run-{suffix}.md", f"benchmark-{suffix}.md"):
            (directory / name).write_text("report\n", encoding="utf-8")

    def test_promotes_all_artifacts_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            destination = Path(temporary) / "baseline"
            source.mkdir()
            self.make_source(source)
            record.promote(source, destination)
            self.assertTrue((destination / "manifest-v2.json").is_file())
            self.assertEqual(set(record.PromotionSpec(2).required), {path.name for path in destination.iterdir() if path.name != "manifest-v2.json"})

    def test_rejects_scored_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            self.make_source(source, scored_failures=1)
            with self.assertRaisesRegex(ValueError, "scored failures"):
                record.validate(source)

    def test_rejects_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            destination = Path(temporary) / "baseline"
            source.mkdir()
            destination.mkdir()
            self.make_source(source)
            with self.assertRaises(FileExistsError):
                record.promote(source, destination)

    def test_v1_remains_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            destination = Path(temporary) / "baseline"
            source.mkdir()
            self.make_source(source, version=1)
            record.promote(source, destination, version=1)
            self.assertTrue((destination / "manifest-v1.json").is_file())


if __name__ == "__main__":
    unittest.main()
