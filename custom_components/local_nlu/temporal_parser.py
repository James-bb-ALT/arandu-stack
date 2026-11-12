"""Timezone-aware normalization and date/time calculation for scheduled jobs."""
from __future__ import annotations

from datetime import datetime, time as dt_time, timedelta
from typing import Any
import math

try:
    from homeassistant.util import dt as dt_util
except ImportError:
    import datetime as dt_util  # type: ignore


def get_now() -> datetime:
    """Return current timezone-aware datetime in HA's local timezone."""
    if hasattr(dt_util, "now"):
        return dt_util.now()
    return datetime.now().astimezone()


def parse_clock_time(time_str: str) -> dt_time | None:
    """Parse 'HH:MM' string into dt_time object."""
    if not time_str or ":" not in time_str:
        return None
    try:
        parts = time_str.strip().split(":")
        h = int(parts[0])
        m = int(parts[1])
        if 0 <= h < 24 and 0 <= m < 60:
            return dt_time(hour=h, minute=m)
    except (ValueError, IndexError):
        return None
    return None


def calculate_due_datetime(
    *,
    delay_seconds: float | None = None,
    time_str: str | None = None,
    date_str: str | None = None,
    recurrence: str | None = None,
) -> datetime | None:
    """Calculate the target datetime for a scheduled action, alarm, or reminder."""
    now = get_now()
    if delay_seconds is not None and delay_seconds > 0:
        return now + timedelta(seconds=delay_seconds)

    target_time = parse_clock_time(time_str) if time_str else None
    if not target_time:
        return None

    # Determine base date
    base_date = now.date()
    if date_str == "amanha":
        base_date = base_date + timedelta(days=1)
    elif date_str and date_str != "hoje":
        try:
            base_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            pass

    target_dt = datetime.combine(base_date, target_time, tzinfo=now.tzinfo)

    # If date was not explicitly tomorrow/future and target_dt is in the past, roll to next occurrence
    if date_str != "amanha" and target_dt <= now:
        target_dt += timedelta(days=1)

    # Recurrence adjustment (e.g. weekdays only)
    if recurrence == "weekdays" and target_dt.weekday() >= 5:
        # 5 is Saturday, 6 is Sunday -> roll forward to Monday
        days_to_add = 7 - target_dt.weekday()
        target_dt += timedelta(days=days_to_add)

    return target_dt


def next_recurrence_datetime(current_due: datetime, recurrence: str | None) -> datetime | None:
    """Calculate next occurrence for a repeating job."""
    if not recurrence:
        return None
    if recurrence == "daily":
        return current_due + timedelta(days=1)
    if recurrence == "weekdays":
        next_dt = current_due + timedelta(days=1)
        while next_dt.weekday() >= 5:
            next_dt += timedelta(days=1)
        return next_dt
    return None
