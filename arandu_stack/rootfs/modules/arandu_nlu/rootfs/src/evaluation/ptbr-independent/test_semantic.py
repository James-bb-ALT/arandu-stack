"""Unit tests for independent semantic validation."""

import unittest

from semantic import Comparison, ValidationError, compare_case, load_json, validate_catalog, validate_response


CATALOG = {
    "version": 1,
    "areas": [{"area_id": "area_sala", "names": ["sala"]}],
    "entities": [
        {
            "registry_id": "reg_light_sala",
            "entity_id": "light.sala",
            "domain": "light",
            "area_id": "area_sala",
            "names": ["luz da sala"],
            "actions": ["get_state", "turn_off", "turn_on"],
        },
        {
            "registry_id": "reg_fan_sala",
            "entity_id": "fan.sala",
            "domain": "fan",
            "area_id": "area_sala",
            "names": ["ventilador da sala"],
            "actions": ["get_state", "set_fan_percentage", "turn_off", "turn_on"],
        },
    ],
}


class SemanticTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = validate_catalog(CATALOG)

    def test_catalog_rejects_unsupported_domain_action(self) -> None:
        invalid = dict(CATALOG)
        invalid["entities"] = [dict(CATALOG["entities"][0], actions=["set_fan_percentage"])]
        self.assertRaises(ValidationError, validate_catalog, invalid)

    def test_target_order_is_not_semantic(self) -> None:
        raw = {
            "status": "plan",
            "version": 1,
            "operations": [{"action": "turn_on", "targets": ["reg_fan_sala", "reg_light_sala"]}],
        }
        expected = validate_response(raw, self.catalog)
        reordered = {
            "status": "plan",
            "version": 1,
            "operations": [{"action": "turn_on", "targets": ["reg_light_sala", "reg_fan_sala"]}],
        }
        self.assertEqual(compare_case(expected, reordered, self.catalog).classification, "exact_pass")

    def test_operation_order_is_semantic(self) -> None:
        expected = validate_response(
            {
                "status": "plan",
                "version": 1,
                "operations": [
                    {"action": "turn_off", "targets": ["reg_light_sala"]},
                    {"action": "turn_on", "targets": ["reg_fan_sala"]},
                ],
            },
            self.catalog,
        )
        actual = {
            "status": "plan",
            "version": 1,
            "operations": [
                {"action": "turn_on", "targets": ["reg_fan_sala"]},
                {"action": "turn_off", "targets": ["reg_light_sala"]},
            ],
        }
        self.assertEqual(compare_case(expected, actual, self.catalog).classification, "semantic_mismatch")

    def test_duplicate_target_is_protocol_error(self) -> None:
        expected = {"status": "no_match", "version": 1}
        actual = {"status": "plan", "version": 1, "operations": [{"action": "turn_on", "targets": ["reg_light_sala", "reg_light_sala"]}]}
        self.assertEqual(compare_case(expected, actual, self.catalog).classification, "protocol_error")

    def test_unknown_target_is_protocol_error(self) -> None:
        actual = {"status": "plan", "version": 1, "operations": [{"action": "turn_on", "targets": ["unknown"]}]}
        self.assertRaises(ValidationError, validate_response, actual, self.catalog)

    def test_percentage_requires_fan_action(self) -> None:
        missing = {"status": "plan", "version": 1, "operations": [{"action": "set_fan_percentage", "targets": ["reg_fan_sala"]}]}
        extra = {"status": "plan", "version": 1, "operations": [{"action": "turn_on", "percentage": 50, "targets": ["reg_light_sala"]}]}
        self.assertRaises(ValidationError, validate_response, missing, self.catalog)
        self.assertRaises(ValidationError, validate_response, extra, self.catalog)

    def test_mixed_read_and_effect_is_protocol_error(self) -> None:
        raw = {
            "status": "plan",
            "version": 1,
            "operations": [
                {"action": "get_state", "targets": ["reg_light_sala"]},
                {"action": "turn_on", "targets": ["reg_fan_sala"]},
            ],
        }
        self.assertRaises(ValidationError, validate_response, raw, self.catalog)

    def test_all_comparison_classes(self) -> None:
        raw_plan = {"status": "plan", "version": 1, "operations": [{"action": "turn_on", "targets": ["reg_light_sala"]}]}
        plan = validate_response(raw_plan, self.catalog)
        rejected = {"status": "no_match", "version": 1}
        self.assertEqual(compare_case(plan, rejected, self.catalog), Comparison("unexpected_rejection", "expected plan, got no_match"))
        self.assertEqual(compare_case(rejected, raw_plan, self.catalog).classification, "unsafe_acceptance")
        self.assertEqual(compare_case(rejected, {"status": "ambiguous", "version": 1}, self.catalog).classification, "semantic_mismatch")
        self.assertEqual(compare_case(rejected, rejected, self.catalog).classification, "exact_pass")

    def test_strict_json_rejects_duplicate_keys_and_nonfinite_values(self) -> None:
        self.assertRaises(ValidationError, load_json, '{"status":"no_match","status":"plan"}')
        self.assertRaises(ValidationError, load_json, '{"number":NaN}')

    def test_boolean_is_not_an_integer(self) -> None:
        self.assertRaises(ValidationError, validate_response, {"status": "no_match", "version": True}, self.catalog)


if __name__ == "__main__":
    unittest.main()
