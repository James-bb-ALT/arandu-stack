from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from stt_pipeline.audio import AudioTools, wav_details
from stt_pipeline.cli import main
from stt_pipeline.common import canonical, digest, identifier, natural_key, read_json, read_jsonl, run, write_json, write_jsonl, write_text
from stt_pipeline.dataset import inspect_dataset, load_mapping, prepare, validate
from stt_pipeline.demo import PHRASES, fixtures, tone
from stt_pipeline.evaluation import alignment, error_counts, evaluate, read_predictions, score_predictions
from stt_pipeline.exporting import check_export_gate
from stt_pipeline.models import checkpoint_path
from stt_pipeline.models import REVISION, UPSTREAM
from stt_pipeline.scripts import parse_script
from stt_pipeline.split import split_records
from stt_pipeline.training import training_config


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "roteiro.txt"
        self.addCleanup(self.temporary.cleanup)

    def parse(self, text, options=None):
        write_text(self.path, text)
        return parse_script(self.path, options)

    def test_numbered_separators_accents_and_headers(self):
        script = self.parse("Roteiro A\n\nSeção: luzes\n001. Acenda a luz da cozinha.\n02) Qual é a temperatura?\n03 - Desligue o ventilador.\n")
        self.assertFalse(script.ambiguous)
        self.assertEqual(list(script.phrases), [1, 2, 3])
        self.assertEqual(script.phrases[2]["text"], "Qual é a temperatura?")

    def test_unnumbered_eighty_phrases(self):
        script = self.parse("# Roteiro\n" + "\n".join(f"Ligue a lâmpada {i}." for i in range(80)))
        self.assertEqual(len(script.phrases), 80)
        self.assertFalse(script.numbered)
        self.assertFalse(script.ambiguous)

    def test_numbered_missing_does_not_shift(self):
        script = self.parse("01. Frase um.\n03. Frase três.")
        self.assertEqual(list(script.phrases), [1, 3])

    def test_duplicate_ids_rejected_including_third_copy(self):
        script = self.parse("1. Um.\n1. Dois.\n1. Três.\n2. Quatro.")
        self.assertNotIn(1, script.phrases)
        self.assertTrue(script.ambiguous)

    def test_possible_header_blocks_and_mapping_resolves(self):
        script = self.parse("Comandos domésticos\nLigue a luz.\nDesligue a luz.")
        self.assertTrue(script.ambiguous)
        corrected = parse_script(self.path, {"exclude_lines": [1]})
        self.assertFalse(corrected.ambiguous)
        self.assertEqual(corrected.phrases[1]["text"], "Ligue a luz.")

    def test_ambiguous_phrase_without_punctuation_include(self):
        script = self.parse("acenda a luz\nFeche a porta.", {"include_lines": [1]})
        self.assertFalse(script.ambiguous)
        self.assertEqual(script.phrases[1]["text"], "acenda a luz")

    def test_mixed_numbering_requires_review(self):
        self.assertTrue(self.parse("1. Acenda a luz.\nDesligue a luz.").ambiguous)

    def test_encoding_is_explicit(self):
        self.path.write_bytes("1. Ação.".encode("cp1252"))
        with self.assertRaises(UnicodeError):
            parse_script(self.path)
        self.assertEqual(parse_script(self.path, {"encoding": "cp1252"}).phrases[1]["text"], "Ação.")

    def test_invalid_line_mapping(self):
        with self.assertRaises(ValueError):
            self.parse("Ligue a luz.", {"exclude_lines": [2]})

    def test_numeric_order_and_compound_extension(self):
        self.assertEqual(identifier(Path("01.m4a.mp4")), 1)
        self.assertIsNone(identifier(Path("roteiro2_frase3.ogg")))
        self.assertEqual(sorted(["10.mp4", "2.ogg", "01.m4a.mp4"], key=natural_key), ["01.m4a.mp4", "2.ogg", "10.mp4"])


class SplitAndMetricTests(unittest.TestCase):
    def records(self):
        return [{"id": f"s{s}_{i}", "speaker": f"s{s}", "text": f"Ligue lâmpada {i}.", "pcm_sha256": f"hash{s}_{i}"}
                for s in range(7) for i in range(30)]

    def test_deterministic_no_phrase_leakage(self):
        records = self.records()
        first = split_records(records, seed=12)
        self.assertEqual(first, split_records(list(reversed(records)), seed=12))
        sets = [{canonical(r["text"]) for r in first[s]} for s in ("train", "validation", "test")]
        self.assertTrue(all(sets[i].isdisjoint(sets[j]) for i in range(3) for j in range(i)))
        self.assertEqual(sum(len(r) for r in first.values()), len(records))

    def test_speaker_shared_scripts_fail_safely(self):
        with self.assertRaisesRegex(ValueError, "independentes"):
            split_records(self.records(), mode="speaker")

    def test_speaker_isolated_scripts(self):
        rows = [{"id": str(i), "speaker": str(i), "text": str(i), "pcm_sha256": str(i)} for i in range(7)]
        result = split_records(rows, mode="speaker")
        self.assertTrue(all(result[s] for s in ("train", "validation", "test")))

    def test_invalid_ratios_and_few_groups(self):
        for ratios in ((.5, .5, .5), (1.1, -.1, 0), (float("nan"), .1, .1)):
            with self.assertRaises(ValueError):
                split_records(self.records(), ratios=ratios)
        with self.assertRaises(ValueError):
            split_records(self.records()[:1])

    def test_wer_cer_edits_and_speaker_aggregation(self):
        records = [{"id": "1", "speaker": "a", "text": "Ligue a luz."}, {"id": "2", "speaker": "b", "text": "Feche porta."}]
        result = score_predictions(records, {"1": "Ligue luz", "2": "Abra porta agora"})
        self.assertEqual(result["overall"]["wer"], 3 / 5)
        self.assertEqual(result["overall"]["deletions"], 1)
        self.assertEqual(result["overall"]["substitutions"], 1)
        self.assertEqual(result["overall"]["insertions"], 1)
        self.assertEqual(result["per_speaker"]["a"]["wer"], 1 / 3)
        self.assertEqual(len(result["word_confusions"]), 3)

    def test_empty_hypothesis_and_accent_preservation(self):
        self.assertEqual(error_counts("ação", "acao")["character_errors"], 2)
        self.assertEqual(error_counts("Ligue a luz.", "")["deletions"], 3)
        self.assertEqual(error_counts("Ligue, a luz!", "ligue a luz")["word_errors"], 0)

    def test_prediction_ids_cannot_be_missing(self):
        with self.assertRaises(ValueError):
            score_predictions([{"id": "x", "speaker": "s", "text": "Frase."}], {})

    def test_int8_not_trainable(self):
        with self.assertRaisesRegex(ValueError, "treinável"):
            checkpoint_path("model.int8.onnx")

    def test_training_defaults_and_bad_options(self):
        self.assertEqual(training_config(None)["batch_size"], 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            write_json(path, {"batch_size": 0})
            with self.assertRaises(ValueError):
                training_config(path)

    def test_export_gate_requires_model_and_unchanged_test_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint, audio = root / "candidate.nemo", root / "audio.wav"
            checkpoint.write_bytes(b"unit-test-checkpoint")
            audio.write_bytes(b"unit-test-audio")
            manifest = root / "manifests" / "test.jsonl"
            write_jsonl(manifest, [{"id": "one", "audio_filepath": str(audio)}])
            report = {"split": "test", "inference_verified": True, "comparison": {"improved": True},
                      "candidate": {"identity": {"sha256": digest(checkpoint)}},
                      "baseline": {"identity": {"upstream": UPSTREAM, "revision": REVISION}},
                      "manifest_sha256": digest(manifest), "audio_fingerprints": {"one": digest(audio)}}
            evidence = root / "comparison.json"
            write_json(evidence, report)
            self.assertTrue(check_export_gate(checkpoint, root, evidence)["comparison"]["improved"])
            audio.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "Áudios"):
                check_export_gate(checkpoint, root, evidence)
            audio.write_bytes(b"unit-test-audio")
            checkpoint.write_bytes(b"different-checkpoint")
            with self.assertRaisesRegex(ValueError, "Checkpoint"):
                check_export_gate(checkpoint, root, evidence)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg/FFprobe necessários")
class MediaTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "input"
        self.output = self.root / "dataset"
        self.addCleanup(self.temporary.cleanup)

    def minimal(self):
        speaker = self.source / "pessoa"
        speaker.mkdir(parents=True)
        write_text(speaker / "roteiro.txt", "\n".join(f"{i}. {PHRASES[i - 1]}" for i in range(1, 5)))
        for i in range(1, 5):
            tone(speaker / f"{i}.wav", frequency=200 + i * 71)
        return speaker

    def test_seven_speakers_real_media_and_incremental(self):
        fixtures(self.source)
        source_hashes = {p: digest(p) for p in self.source.rglob("*") if p.is_file()}
        inspection = inspect_dataset(self.source, self.root / "inspection")
        self.assertEqual(len(inspection["scripts"]), 7)
        formats = {r["source"]: r.get("codec") for r in inspection["files"]}
        self.assertEqual(formats["participante_01/01.ogg"], "vorbis")
        self.assertEqual(formats["participante_02/01.mp4"], "aac")
        self.assertEqual(formats["participante_03/01.m4a.mp4"], "aac")
        stats = prepare(self.source, self.output)
        self.assertEqual(stats["participants"], 7)
        self.assertEqual(stats["rejected_examples"], 4)  # Two duplicate IDs, corrupt OGG, empty MP4.
        self.assertTrue(validate(self.output)["ready"])
        metadata = read_jsonl(self.output / "metadata.jsonl")
        third = next(r for r in metadata if r["speaker"] == "participante_01" and r["phrase_id"] == 3)
        self.assertEqual(third["text"], PHRASES[2])
        unnumbered = [r for r in metadata if r["speaker"] == "participante_07"]
        self.assertEqual(len(unnumbered), 8)
        for row in metadata:
            with wave.open(row["audio_filepath"], "rb") as f:
                self.assertEqual((f.getframerate(), f.getnchannels(), f.getsampwidth()), (16000, 1, 2))
        manifests = {p: p.read_bytes() for p in (self.output / "manifests").glob("*")}
        mtimes = {Path(r["audio_filepath"]): Path(r["audio_filepath"]).stat().st_mtime_ns for r in metadata}
        prepare(self.source, self.output)
        self.assertEqual(manifests, {p: p.read_bytes() for p in manifests})
        self.assertEqual(mtimes, {p: p.stat().st_mtime_ns for p in mtimes})
        self.assertTrue(all(r["cached"] for r in read_jsonl(self.output / "metadata.jsonl")))
        self.assertEqual(source_hashes, {p: digest(p) for p in source_hashes})

    def test_wrong_extension_and_corrupt_cache_reprocessed(self):
        speaker = self.minimal()
        (speaker / "1.wav").rename(speaker / "1.ogg")
        result = prepare(self.source, self.output)
        self.assertEqual(result["valid_examples"], 4)
        row = next(r for r in read_jsonl(self.output / "metadata.jsonl") if r["phrase_id"] == 1)
        self.assertEqual(row["codec"], "pcm_s16le")
        Path(row["audio_filepath"]).write_bytes(b"corrupt")
        self.assertFalse(validate(self.output)["ready"])
        prepare(self.source, self.output)
        self.assertTrue(validate(self.output)["ready"])

    def test_multiple_audio_streams_require_selection(self):
        first, second, container = self.root / "first.wav", self.root / "second.wav", self.root / "both.mkv"
        tone(first, frequency=300)
        tone(second, frequency=700)
        run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(first), "-i", str(second),
             "-map", "0:a", "-map", "1:a", "-c:a", "pcm_s16le", "-disposition:a:0", "0", "-disposition:a:1", "0", str(container)])
        tools = AudioTools()
        with self.assertRaisesRegex(ValueError, "inequívoco"):
            tools.probe(container)
        info = tools.probe(container, stream_index=1)
        result = tools.convert(container, self.root / "selected.wav", info, 16000, digest(container), {})
        reference = tools.convert(second, self.root / "reference.wav", tools.probe(second), 16000, digest(second), {})
        self.assertEqual(result["pcm_sha256"], reference["pcm_sha256"])

    def test_video_container_selects_audio_stream(self):
        audio, video = self.root / "tone.wav", self.root / "video.mp4"
        tone(audio)
        run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i", "color=c=black:s=16x16:r=10:d=0.6",
             "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "mpeg4", "-c:a", "aac", "-shortest", str(video)])
        tools = AudioTools()
        info = tools.probe(video)
        self.assertEqual(info["stream_index"], 1)
        result = tools.convert(video, self.root / "extracted.wav", info, 16000, digest(video), {})
        self.assertGreater(result["duration"], .5)

    def test_decoded_duplicates_and_manual_skip(self):
        speaker = self.minimal()
        shutil.copyfile(speaker / "1.wav", speaker / "2.wav")
        inspection = inspect_dataset(self.source, self.root / "inspection")
        self.assertEqual(sum(r.get("code") == "duplicate_source" for r in inspection["files"]), 2)
        # Different containers and bytes, identical decoded PCM.
        run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(speaker / "2.wav"), "-c:a", "flac", str(speaker / "2.flac")])
        (speaker / "2.wav").unlink()
        stats = prepare(self.source, self.output, ratios=(1, 0, 0))
        self.assertEqual(stats["rejected_examples"], 2)
        self.assertEqual(stats["valid_examples"], 2)
        mapping = self.root / "mapping.json"
        write_json(mapping, {"audio": {"pessoa/2.flac": {"skip": True}}})
        stats = prepare(self.source, self.output, mapping_path=mapping)
        self.assertEqual(stats["valid_examples"], 3)
        self.assertEqual(stats["skipped_examples"], 1)

    def test_missing_script_phrase_no_shift(self):
        speaker = self.minimal()
        write_text(speaker / "roteiro.txt", "1. Frase um.\n3. Frase três.\n4. Frase quatro.")
        stats = prepare(self.source, self.output)
        self.assertEqual(stats["rejected_examples"], 1)
        third = next(r for r in read_jsonl(self.output / "metadata.jsonl") if r["phrase_id"] == 3)
        self.assertEqual(third["text"], "Frase três.")

    def test_unnumbered_missing_transcript_blocks_automatic_shift(self):
        speaker = self.minimal()
        write_text(speaker / "roteiro.txt", "\n".join(PHRASES[:3]))
        result = prepare(self.source, self.output)
        self.assertEqual(result["valid_examples"], 0)
        self.assertEqual(result["rejected_examples"], 4)

    def test_multiple_scripts_require_explicit_mapping(self):
        speaker = self.minimal()
        shutil.copyfile(speaker / "roteiro.txt", speaker / "outro.txt")
        self.assertEqual(prepare(self.source, self.output)["valid_examples"], 0)
        mapping = self.root / "mapping.json"
        write_json(mapping, {"audio": {f"pessoa/{i}.wav": {"script": "pessoa/roteiro.txt", "phrase_id": i} for i in range(1, 5)}})
        self.assertEqual(prepare(self.source, self.output, mapping_path=mapping)["valid_examples"], 4)

    def test_silence_and_duration_rejected(self):
        speaker = self.minimal()
        tone(speaker / "1.wav", frequency=0)
        tone(speaker / "2.wav", frequency=330, duration=.05)
        stats = prepare(self.source, self.output, ratios=(1, 0, 0))
        self.assertEqual(stats["rejected_examples"], 2)

    def test_source_change_invalidates_cache(self):
        speaker = self.minimal()
        prepare(self.source, self.output)
        old = next(r for r in read_jsonl(self.output / "metadata.jsonl") if r["phrase_id"] == 1)
        tone(speaker / "1.wav", frequency=987)
        prepare(self.source, self.output)
        new = next(r for r in read_jsonl(self.output / "metadata.jsonl") if r["phrase_id"] == 1)
        self.assertFalse(new["cached"])
        self.assertNotEqual(old["pcm_sha256"], new["pcm_sha256"])

    def test_validation_rejects_tampered_text_and_leakage(self):
        self.minimal()
        prepare(self.source, self.output)
        path = self.output / "manifests" / "test.jsonl"
        rows = read_jsonl(path)
        rows[0]["text"] = read_jsonl(self.output / "manifests" / "train.jsonl")[0]["text"]
        write_jsonl(path, rows)
        self.assertFalse(validate(self.output)["ready"])
        self.assertFalse(validate(self.output)["ready"])

    def test_incomplete_preparation_does_not_release_old_dataset(self):
        self.minimal()
        prepare(self.source, self.output)
        write_json(self.output / "reports" / "release.json", {"ready": False, "reason": "preparation_in_progress"})
        self.assertFalse(validate(self.output)["ready"])
        self.assertFalse(validate(self.output)["ready"])

    def test_predictions_comparison_cannot_unlock_export(self):
        self.minimal()
        prepare(self.source, self.output)
        records = read_jsonl(self.output / "manifests" / "test.jsonl")
        baseline, candidate = self.root / "baseline.jsonl", self.root / "candidate.jsonl"
        write_jsonl(baseline, [{"id": r["id"], "text": "frase errada"} for r in records])
        write_jsonl(candidate, [{"id": r["id"], "text": r["text"]} for r in records])
        comparison = evaluate(self.output, self.root / "evaluation", predictions=candidate, baseline_predictions=baseline)
        self.assertTrue(comparison["comparison"]["improved"])
        self.assertFalse(comparison["inference_verified"])
        fake_model = self.root / "fake.nemo"
        fake_model.write_bytes(b"test checkpoint")
        with self.assertRaisesRegex(ValueError, "inferência"):
            check_export_gate(fake_model, self.output, self.root / "evaluation" / "comparison.json")

    def test_input_output_separation_and_unknown_mapping(self):
        self.minimal()
        with self.assertRaises(ValueError):
            prepare(self.source, self.source / "dataset")
        mapping = self.root / "mapping.json"
        write_json(mapping, {"audio": {"pessoa/absent.wav": {"skip": True}}})
        with self.assertRaises(ValueError):
            prepare(self.source, self.output, mapping_path=mapping)

    def test_acoustic_review_never_creates_training_manifests(self):
        import review_recordings
        self.minimal()
        def fake_prediction(records, *args, **kwargs):
            return {r["id"]: PHRASES[int(Path(r["source"]).stem) - 1] for r in records}, {}, {"type": "unit_test_stub"}
        with patch.object(review_recordings, "predict", side_effect=fake_prediction):
            summary = review_recordings.review(self.source, self.root / "review", Path("not-used"))
        self.assertEqual(summary["decoded"], 4)
        self.assertFalse((self.root / "review" / "manifests").exists())
        report = read_json(self.root / "review" / "review.json")
        self.assertTrue(all(row["association_confirmed"] is False for row in report["rows"]))
        self.assertEqual({row["proposed_phrase_id"] for row in report["rows"]}, {1, 2, 3, 4})


if __name__ == "__main__":
    unittest.main()
