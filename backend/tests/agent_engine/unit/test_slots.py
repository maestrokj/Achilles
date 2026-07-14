"""Slot expansion: interval anchoring + calendar in the owner's timezone (P0)."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from achilles.agent_engine.constants import CalendarCadence, ScheduleKind
from achilles.agent_engine.scheduler.slots import next_slot
from achilles.agent_engine.schemas import CalendarSchedule, IntervalSchedule

pytestmark = [pytest.mark.unit, pytest.mark.p0]

UTC_TZ = ZoneInfo("UTC")
MOSCOW = ZoneInfo("Europe/Moscow")  # UTC+3, no DST

# Wednesday 2026-07-01 12:00 UTC.
NOW = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


def interval(hours: int) -> IntervalSchedule:
    return IntervalSchedule(type=ScheduleKind.INTERVAL, every_hours=hours)


def calendar(cadence: CalendarCadence, time: str, weekday: int | None = None) -> CalendarSchedule:
    return CalendarSchedule(type=ScheduleKind.CALENDAR, cadence=cadence, weekday=weekday, time=time)


def test_interval_counts_from_the_previous_start() -> None:
    base = NOW - timedelta(hours=1)
    assert next_slot(interval(6), tz=UTC_TZ, now=NOW, base=base) == base + timedelta(hours=6)


def test_interval_without_base_counts_from_now() -> None:
    assert next_slot(interval(2), tz=UTC_TZ, now=NOW) == NOW + timedelta(hours=2)


def test_interval_stale_anchor_never_schedules_into_the_past() -> None:
    base = NOW - timedelta(hours=48)
    assert next_slot(interval(6), tz=UTC_TZ, now=NOW, base=base) == NOW


def test_calendar_daily_resolves_in_owner_timezone() -> None:
    # 09:00 Moscow = 06:00 UTC; already past at 12:00 UTC → tomorrow.
    slot = next_slot(calendar(CalendarCadence.DAILY, "09:00"), tz=MOSCOW, now=NOW)
    assert slot == datetime(2026, 7, 2, 6, 0, tzinfo=UTC)


def test_calendar_daily_later_today_stays_today() -> None:
    # 23:00 Moscow = 20:00 UTC, still ahead of 12:00 UTC.
    slot = next_slot(calendar(CalendarCadence.DAILY, "23:00"), tz=MOSCOW, now=NOW)
    assert slot == datetime(2026, 7, 1, 20, 0, tzinfo=UTC)


def test_calendar_weekly_targets_the_weekday() -> None:
    # NOW is Wednesday (weekday 2); Monday (0) 08:00 UTC → next Monday.
    slot = next_slot(calendar(CalendarCadence.WEEKLY, "08:00", weekday=0), tz=UTC_TZ, now=NOW)
    assert slot == datetime(2026, 7, 6, 8, 0, tzinfo=UTC)
    assert slot.weekday() == 0


def test_calendar_weekly_same_day_past_time_waits_a_week() -> None:
    slot = next_slot(calendar(CalendarCadence.WEEKLY, "08:00", weekday=2), tz=UTC_TZ, now=NOW)
    assert slot == datetime(2026, 7, 8, 8, 0, tzinfo=UTC)


def test_weekly_needs_weekday() -> None:
    with pytest.raises(ValueError, match="weekday"):
        CalendarSchedule(type=ScheduleKind.CALENDAR, cadence=CalendarCadence.WEEKLY, time="08:00")
