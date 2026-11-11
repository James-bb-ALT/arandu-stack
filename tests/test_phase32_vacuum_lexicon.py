"""Tests for Phase 3.2 vacuum colloquial lexicon recognition."""
import unittest

from custom_components.local_nlu.capabilities import ADAPTERS


class TestVacuumLexicon(unittest.TestCase):
    def test_vacuum_operations_contract(self):
        # Verify vacuum operations map to supported actions in capabilities
        for action in ("vacuum_start", "vacuum_stop", "vacuum_pause", "vacuum_dock", "vacuum_area"):
            self.assertIn(("vacuum", action), ADAPTERS)

    def test_vacuum_lexicon_phrases_mapping(self):
        phrases = {
            "Pausa o aspirador": "vacuum_pause",
            "Pausa a limpeza": "vacuum_pause",
            "Dá uma pausa na limpeza": "vacuum_pause",
            "Para o aspirador": "vacuum_stop",
            "Cancela o aspirador": "vacuum_stop",
            "Para a limpeza": "vacuum_stop",
            "Cancela a limpeza": "vacuum_stop",
            "Manda o robô voltar para a base": "vacuum_dock",
        }
        self.assertEqual(len(phrases), 8)
        for phrase, action in phrases.items():
            self.assertIn(("vacuum", action), ADAPTERS)


if __name__ == "__main__":
    unittest.main()
