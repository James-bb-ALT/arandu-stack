"""Tests for Phase 3.2 room and device bulk exclusions."""
import unittest
from unittest.mock import Mock, patch

from custom_components.local_nlu.contextual_catalog import Snapshot
from custom_components.local_nlu.contextual_protocol import Operation
from custom_components.local_nlu.contextual_runtime import ContextualRuntime, ExecutionError


class TestPhase32Exclusions(unittest.TestCase):
    def setUp(self):
        self.hass = Mock()
        self.client = Mock()
        self.runtime = ContextualRuntime(self.hass, self.client)

    def tearDown(self):
        self.runtime.close()

    def test_validate_bulk_snapshot_with_exclusion(self):
        by_id = {
            "reg_light": {
                "registry_id": "reg_light",
                "entity_id": "light.quarto",
                "domain": "light",
                "area_id": "quarto",
                "actions": ["turn_on", "turn_off"],
                "attributes": {"available": True, "bulk_eligible": True},
            },
            "reg_fan": {
                "registry_id": "reg_fan",
                "entity_id": "fan.quarto",
                "domain": "fan",
                "area_id": "quarto",
                "actions": ["turn_on", "turn_off"],
                "attributes": {"available": True, "bulk_eligible": True},
            },
            "reg_tv": {
                "registry_id": "reg_tv",
                "entity_id": "media_player.tv_quarto",
                "domain": "media_player",
                "area_id": "quarto",
                "actions": ["turn_on", "turn_off"],
                "attributes": {"available": True, "bulk_eligible": True},
            },
        }
        snapshot = Snapshot(
            payload={"generation": "gen1", "groups": []},
            by_id=by_id,
        )

        op = Operation(
            intent="device.turn_off",
            action="turn_off",
            targets=("reg_fan", "reg_light"),
            parameters={
                "scope": "bulk_area",
                "area": "quarto",
                "excluded_mentions": ["tv"],
                "excluded_targets": ["reg_tv"],
            },
            evidence=("bulk_area", "complete_spatial_set"),
        )

        user = Mock()
        options = {}

        with patch("custom_components.local_nlu.contextual_catalog.build", return_value=snapshot):
            self.runtime.validate_bulk_snapshot((op,), snapshot, user, options)

    def test_validate_bulk_snapshot_missing_exclusion_raises(self):
        by_id = {
            "reg_light": {
                "registry_id": "reg_light",
                "entity_id": "light.quarto",
                "domain": "light",
                "area_id": "quarto",
                "actions": ["turn_on", "turn_off"],
                "attributes": {"available": True, "bulk_eligible": True},
            },
            "reg_tv": {
                "registry_id": "reg_tv",
                "entity_id": "media_player.tv_quarto",
                "domain": "media_player",
                "area_id": "quarto",
                "actions": ["turn_on", "turn_off"],
                "attributes": {"available": True, "bulk_eligible": True},
            },
        }
        snapshot = Snapshot(
            payload={"generation": "gen1", "groups": []},
            by_id=by_id,
        )

        op = Operation(
            intent="device.turn_off",
            action="turn_off",
            targets=("reg_light",),
            parameters={
                "scope": "bulk_area",
                "area": "quarto",
            },
            evidence=("bulk_area", "complete_spatial_set"),
        )

        user = Mock()
        options = {}
        with patch("custom_components.local_nlu.contextual_catalog.build", return_value=snapshot):
            with self.assertRaises(ExecutionError) as ctx:
                self.runtime.validate_bulk_snapshot((op,), snapshot, user, options)
            self.assertEqual(ctx.exception.reason, "bulk_target_set_changed")


if __name__ == "__main__":
    unittest.main()
