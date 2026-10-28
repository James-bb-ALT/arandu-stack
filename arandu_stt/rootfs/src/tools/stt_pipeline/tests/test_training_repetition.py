import sys
from pathlib import Path
import unittest
import os
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from stt_pipeline.cached_training import training_order
from run_rtx_experiment import word_diagnostics
from stt_pipeline.common import write_text


class TrainingRepetitionTests(unittest.TestCase):
    def test_progress_write_retries_transient_windows_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "progress.json"
            target.write_text("previous", encoding="utf-8")
            real_replace = os.replace
            attempts = []
            def transient_lock(source, destination):
                attempts.append(source)
                if len(attempts) == 1:
                    raise PermissionError("reader holds file")
                real_replace(source, destination)
            with patch("stt_pipeline.common.os.replace", side_effect=transient_lock), patch("stt_pipeline.common.time.sleep"):
                write_text(target, "complete update")
            self.assertEqual(target.read_text(encoding="utf-8"), "complete update")
            self.assertEqual(len(attempts), 2)
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_permanent_lock_preserves_previous_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "progress.json"
            target.write_text("previous", encoding="utf-8")
            with patch("stt_pipeline.common.os.replace", side_effect=PermissionError("locked")), patch("stt_pipeline.common.time.sleep"):
                with self.assertRaises(PermissionError):
                    write_text(target, "new update")
            self.assertEqual(target.read_text(encoding="utf-8"), "previous")
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_uniform_behavior_and_target_sampling(self):
        regular = {"id": "regular"}
        focus = {"id": "focus", "training_repeats": 3}
        rows = [regular, focus]
        self.assertEqual([row["id"] for row in training_order(rows)], ["regular", "focus", "focus", "focus"])
        self.assertEqual(len(rows), 2)
        self.assertNotIn("training_repeats", regular)

    def test_invalid_repeats_fail_before_training(self):
        for invalid in (0, 11, 1.5, True, "3"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                training_order([{"id": "invalid", "training_repeats": invalid}])

    def test_focus_report_keeps_train_and_heldout_separate(self):
        rows = [{"id": "train", "split": "train", "text": "Jaime, Jaime, Jaime"},
                {"id": "test", "split": "test", "text": "Liga o ventilador"}]
        previous = {"train": "Jaime, jaine, Jaime", "test": "Liga o ventidor"}
        current = {"train": "Jaime, Jaime, Jaime", "test": "Liga o ventilador"}
        report = word_diagnostics(rows, previous, current)
        self.assertEqual(report["train"]["scope"], "training_diagnostic")
        self.assertEqual(report["train"]["jaime"]["candidate"], {"occurrences": 3, "correct": 3})
        self.assertEqual(report["train"]["jaime"]["baseline"]["correct"], 2)
        self.assertEqual(report["test"]["ventilador"]["candidate"]["correct"], 1)
        self.assertEqual(report["validation"]["jaime"]["candidate"]["occurrences"], 0)


if __name__ == "__main__":
    unittest.main()
