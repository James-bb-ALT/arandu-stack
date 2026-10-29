import unittest

from wakegen.voice_bank import assess_diversity


class DiversityTests(unittest.TestCase):
    def assess(self, vector, checksum="new"):
        return assess_diversity({"speaker_embedding": vector, "reference_sha256": checksum},
            [{"speaker_id": "old", "speaker_embedding": [1., 0.], "reference_sha256": "old"}], .94)

    def test_high_uncalibrated_cosine_is_review_not_rejection(self):
        result = self.assess([.98, .15])
        self.assertTrue(result["requires_review"])
        self.assertIsNone(result["rejection_reason"])
        self.assertEqual(result["identity_distinctness"], "not_verified")

    def test_identical_reference_remains_rejected(self):
        self.assertEqual(self.assess([0., 1.], "old")["rejection_reason"], "duplicate_reference_audio")

    def test_identical_embedding_remains_rejected(self):
        self.assertEqual(self.assess([1., 0.])["rejection_reason"], "identical_speaker_embedding")

    def test_dissimilar_embedding_needs_no_similarity_review(self):
        result = self.assess([0., 1.])
        self.assertFalse(result["requires_review"])
        self.assertIsNone(result["rejection_reason"])
