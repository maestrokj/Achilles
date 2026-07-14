"""Backup due-ness: daily/weekly windows, no backfill of missed windows (unit)."""

from datetime import UTC, datetime, timedelta

import pytest

from achilles.knowledge_store.constants import CadenceFrequency
from achilles.knowledge_store.services.backup_schedule import (
    WindowCadence,
    is_due,
    last_fire,
    next_fire,
)

pytestmark = [pytest.mark.unit]


def settings_row(
    *,
    frequency: str = CadenceFrequency.DAILY.value,
    weekday: int | None = None,
    time: str = "02:00",
) -> WindowCadence:
    return WindowCadence(frequency=frequency, weekday=weekday, time=time)


def test_daily_fire_today_when_window_passed():
    now = datetime(2026, 7, 2, 10, 0, tzinfo=UTC)
    assert last_fire(settings_row(), timezone="UTC", now=now) == datetime(
        2026, 7, 2, 2, 0, tzinfo=UTC
    )


def test_daily_fire_yesterday_when_window_ahead():
    now = datetime(2026, 7, 2, 1, 0, tzinfo=UTC)
    assert last_fire(settings_row(), timezone="UTC", now=now) == datetime(
        2026, 7, 1, 2, 0, tzinfo=UTC
    )


def test_weekly_fire_on_the_configured_weekday():
    # 2026-07-02 is a Thursday; weekday 2 = Wednesday.
    now = datetime(2026, 7, 2, 10, 0, tzinfo=UTC)
    row = settings_row(frequency=CadenceFrequency.WEEKLY.value, weekday=2, time="03:00")
    assert last_fire(row, timezone="UTC", now=now) == datetime(2026, 7, 1, 3, 0, tzinfo=UTC)


def test_weekly_steps_a_full_week_back_when_window_ahead():
    # Wednesday 01:00, window Wednesday 03:00 → last fire is the previous Wednesday.
    now = datetime(2026, 7, 1, 1, 0, tzinfo=UTC)
    row = settings_row(frequency=CadenceFrequency.WEEKLY.value, weekday=2, time="03:00")
    assert last_fire(row, timezone="UTC", now=now) == datetime(2026, 6, 24, 3, 0, tzinfo=UTC)


def test_window_is_expanded_in_the_org_timezone():
    # 02:00 Moscow (UTC+3) → 23:00 UTC of the previous day.
    now = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)
    fire = last_fire(settings_row(), timezone="Europe/Moscow", now=now)
    assert fire == datetime(2026, 7, 1, 23, 0, tzinfo=UTC)


def test_next_fire_keeps_the_local_window_across_dst():
    # Berlin leaves DST on 2026-10-25 (CEST +2 → CET +1): the 03:00 window is
    # 01:00 UTC before the shift and 02:00 UTC after — a 25-hour step, not +24h.
    now = datetime(2026, 10, 24, 10, 0, tzinfo=UTC)
    fire = next_fire(settings_row(time="03:00"), timezone="Europe/Berlin", now=now)
    assert fire == datetime(2026, 10, 25, 2, 0, tzinfo=UTC)


def test_next_fire_is_one_period_after_the_last():
    now = datetime(2026, 7, 2, 10, 0, tzinfo=UTC)
    assert next_fire(settings_row(), timezone="UTC", now=now) == datetime(
        2026, 7, 3, 2, 0, tzinfo=UTC
    )


def test_due_when_no_snapshot_yet():
    now = datetime(2026, 7, 2, 10, 0, tzinfo=UTC)
    assert is_due(settings_row(), last_started_at=None, timezone="UTC", now=now) is not None


def test_not_due_when_snapshot_covers_the_window():
    now = datetime(2026, 7, 2, 10, 0, tzinfo=UTC)
    covered = datetime(2026, 7, 2, 2, 5, tzinfo=UTC)
    assert is_due(settings_row(), last_started_at=covered, timezone="UTC", now=now) is None


def test_missed_windows_are_not_backfilled():
    """Three days of downtime → exactly one fire moment (the latest), not three."""
    now = datetime(2026, 7, 2, 10, 0, tzinfo=UTC)
    stale = now - timedelta(days=3)
    fire = is_due(settings_row(), last_started_at=stale, timezone="UTC", now=now)
    assert fire == datetime(2026, 7, 2, 2, 0, tzinfo=UTC)
