"""Tests for Phase 3.2 vacuum colloquial lexicon recognition."""
import unittest
from unittest.mock import Mock

from custom_components.local_nlu.contextual_catalog import Snapshot
from custom_components.local_nlu.contextual_protocol import Operation
from custom_components.local_nlu.contextual_runtime import ContextualRuntime


class TestVacuumLexicon(unittest.TestCase):
    def setUp(self):
        self.hass = Mock()
        self.client = Mock()
        self.runtime = ContextualRuntime(self.hass, self.client)

    def tearDown(self):
        self.runtime.close()

    def test_vacuum_operations_contract(self):
        # Verify vacuum operations map to supported actions in capabilities
        from custom_components.local_nlu.capabilities import ADAPTERS

        for action in ("vacuum_start", "vacuum_stop", "vacuum_pause", "vacuum_dock", "vacuum_area"):
            self.assertIn(("vacuum", action), ADAPTERS)


if __name__ == "__main__":
    unittest.main()
