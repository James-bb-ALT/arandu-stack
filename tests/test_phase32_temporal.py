"""Unit tests for Phase 3.2 temporal parsing and protocol extensions."""
from datetime import datetime, time as dt_time, timedelta, timezone
import unittest

from custom_components.local_nlu.contextual_protocol import (
    Operation,
    Outcome,
    parse,
    validate_parameters,
)
from custom_components.local_nlu.temporal_parser import (
    calculate_due_datetime,
    get_now,
    next_recurrence_datetime,
    parse_clock_time,
)


class TestTemporalParser(unittest.TestCase):
    def test_parse_clock_time(self):
        self.assertEqual(parse_clock_time("18:00"), dt_time(18, 0))
        self.assertEqual(parse_clock_time("07:30"), dt_time(7, 30))
        self.assertEqual(parse_clock_time("00:00"), dt_time(0, 0))
        self.assertEqual(parse_clock_time("23:59"), dt_time(23, 59))
        self.assertIsNone(parse_clock_time("24:00"))
        self.assertIsNone(parse_clock_time("18:60"))
        self.assertIsNone(parse_clock_time("invalid"))
        self.assertIsNone(parse_clock_time(""))

    def test_calculate_due_datetime_with_delay(self):
        now = get_now()
        due = calculate_due_datetime(delay_seconds=600.0)
        self.assertIsNotNone(due)
        diff = (due - now).total_seconds()
        self.assertAlmostEqual(diff, 600.0, delta=2.0)

    def test_calculate_due_datetime_clock_time(self):
        now = get_now()
        due = calculate_due_datetime(time_str="18:00")
        self.assertIsNotNone(due)
        self.assertEqual(due.hour, 18)
        self.assertEqual(due.minute, 0)
        self.assertGreater(due, now)

    def test_calculate_due_datetime_tomorrow(self):
        now = get_now()
        due = calculate_due_datetime(time_str="07:00", date_str="amanha")
        self.assertIsNotNone(due)
        self.assertEqual(due.hour, 7)
        self.assertEqual(due.minute, 0)
        expected_date = (now + timedelta(days=1)).date()
        self.assertEqual(due.date(), expected_date)

    def test_recurrence_next_datetime(self):
        base = datetime(2026, 11, 11, 7, 0, tzinfo=timezone.utc)  # Wednesday
        # Daily
        daily_next = next_recurrence_datetime(base, "daily")
        self.assertEqual(daily_next, datetime(2026, 11, 12, 7, 0, tzinfo=timezone.utc))

        # Weekdays from Friday (2026-11-13) rolls to Monday (2026-11-16)
        friday = datetime(2026, 11, 13, 7, 0, tzinfo=timezone.utc)
        weekdays_next = next_recurrence_datetime(friday, "weekdays")
        self.assertEqual(weekdays_next, datetime(2026, 11, 16, 7, 0, tzinfo=timezone.utc))
        self.assertEqual(weekdays_next.weekday(), 0)  # Monday


class TestProtocolPhase32(unittest.TestCase):
    def test_validate_parameters_new_fields(self):
        params = {
            "name": "arroz",
            "title": "tomar remedio",
            "time": "18:00",
            "duration": 600.0,
            "delay": 300.0,
            "recurrence": "daily",
            "target_action": "turn_on",
            "target_domain": "light",
            "excluded_mentions": ["tv", "ventilador"],
            "excluded_targets": ["reg_tv", "reg_fan"],
            "excluded_device_ids": ["dev_tv"],
        }
        validated = validate_parameters(params)
        self.assertEqual(validated["name"], "arroz")
        self.assertEqual(validated["duration"], 600.0)
        self.assertEqual(validated["excluded_targets"], ["reg_tv", "reg_fan"])

    def test_parse_v4_outcome_empty_targets_for_temporal_actions(self):
        payload = {
            "version": 4,
            "status": "plan",
            "operations": [
                {
                    "intent": "clock_alarm.create",
                    "action": "clock_alarm_create",
                    "targets": [],
                    "parameters": {"time": "07:00", "recurrence": "daily"},
                    "evidence": ["typed_intent"],
                    "depends_on": [],
                }
            ],
        }
        outcome = parse(payload)
        self.assertEqual(outcome.status, "plan")
        self.assertEqual(len(outcome.operations), 1)
        op = outcome.operations[0]
        self.assertEqual(op.action, "clock_alarm_create")
        self.assertEqual(op.targets, ())
        self.assertEqual(op.parameters["time"], "07:00")


if __name__ == "__main__":
    unittest.main()
