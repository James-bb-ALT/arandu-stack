from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE/'benchmarks'))
from calibrate import fit, predict
from evaluate import errors


class TestEvaluation(unittest.TestCase):
    def test_word_character_counts(self):
        result = errors('liga a luz', 'liga luz')
        self.assertEqual(result['words'], 3)
        self.assertEqual(result['word_errors'], 1)
        self.assertEqual(result['char_errors'], 2)
        self.assertEqual(errors('luz', 'luz extra')['word_errors'], 1)

    def test_isotonic_calibration_and_ties(self):
        rows = [{'confidence': .1, 'correct': True}]*5 + [{'confidence': .2, 'correct': False}]*5
        rows += [{'confidence': .8, 'correct': True}]*10
        blocks = fit(rows)
        self.assertEqual(predict(blocks, .15), .5)
        self.assertEqual(predict(blocks, .9), 1)
        self.assertEqual(blocks[0]['count'], 10)

    def test_calibration_rejects_insufficient_or_invalid_labels(self):
        with self.assertRaises(ValueError):
            fit([{'confidence': .5, 'correct': True}]*20)
        with self.assertRaises(ValueError):
            fit([{'confidence': float('nan'), 'correct': bool(i%2)} for i in range(20)])
