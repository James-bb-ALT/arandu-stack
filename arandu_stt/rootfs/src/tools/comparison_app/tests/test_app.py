from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS / "comparison_app"))
from fastapi.testclient import TestClient
from server import create_app
from stt_pipeline.common import digest, read_jsonl, write_json, write_jsonl
from stt_pipeline.cached_training import relocate_dataset
from stt_pipeline.demo import tone


class ComparisonAppTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        audio = self.root / "dataset" / "audio" / "speaker" / "one.wav"
        tone(audio, sample_rate=16000, channels=1)
        self.row = {"id": "one", "speaker": "speaker", "text": "Acenda a luz.", "duration": .6, "audio_filepath": str(audio)}
        write_jsonl(self.root / "dataset" / "metadata.jsonl", [self.row])
        for split in ("train", "validation", "test"):
            write_jsonl(self.root / "dataset" / "manifests" / f"{split}.jsonl", [self.row] if split == "test" else [])
        write_json(self.root / "dataset" / "reports" / "statistics.json", {"valid_examples": 1})
        self.experiment = self.root / "experiment"
        self.client = TestClient(create_app(self.root, self.experiment))

    def test_private_files_are_only_accessible_by_dataset_id(self):
        self.assertEqual(self.client.get("/api/audio/one").status_code, 200)
        self.assertEqual(self.client.get("/api/audio/unknown").status_code, 404)
        self.assertEqual(self.client.get("/api/compare/unknown").status_code, 404)
        self.assertEqual(self.client.get("/api/audio/%2E%2E%2Fsecret").status_code, 404)

    def test_pending_model_never_claims_candidate_metrics(self):
        state = self.client.get("/api/status").json()
        self.assertFalse(state["candidate_available"])
        self.assertIsNone(state["comparison"])
        self.assertEqual(state["progress"]["phase"], "environment")

    def test_partial_onnx_does_not_become_available(self):
        model = self.experiment / "export" / "int8"
        model.mkdir(parents=True)
        (model / "model.int8.onnx").write_bytes(b"partial")
        (model / "tokens.txt").write_text("token 0", encoding="utf-8")
        self.assertFalse(self.client.get("/api/status").json()["candidate_available"])

    def test_upload_errors_are_explicit(self):
        self.assertEqual(self.client.post("/api/compare-upload", content=b"").status_code, 422)
        self.assertEqual(self.client.post("/api/compare-upload", content=b"x" * (15 * 1024 * 1024 + 1)).status_code, 413)

    def test_linux_relocation_preserves_original_manifests_and_audio(self):
        source = self.root / "dataset"
        before = (source / "manifests" / "test.jsonl").read_bytes()
        audio_hash = digest(Path(self.row["audio_filepath"]))
        destination = self.root / "linux_dataset"
        relocate_dataset(source, destination)
        relocated = read_jsonl(destination / "manifests" / "test.jsonl")[0]
        self.assertEqual((source / "manifests" / "test.jsonl").read_bytes(), before)
        self.assertEqual(digest(Path(relocated["audio_filepath"])), audio_hash)
        self.assertEqual(relocated["id"], self.row["id"])
        self.assertNotEqual(relocated["audio_filepath"], self.row["audio_filepath"])


if __name__ == "__main__":
    unittest.main()
