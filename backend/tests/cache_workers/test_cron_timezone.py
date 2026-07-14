"""Cron windows: naive org-time → UTC at fire; DST; no backfill (unit)."""

from datetime import UTC, datetime

import pytest

from achilles.infra.scheduler.cron import next_utc_fire

pytestmark = [pytest.mark.unit]


def test_expands_org_timezone_to_utc():
    now = datetime(2026, 7, 2, 10, 0, tzinfo=UTC)
    fire = next_utc_fire("14:30", timezone="Europe/Moscow", now=now)
    assert fire == datetime(2026, 7, 2, 11, 30, tzinfo=UTC)  # MSK = UTC+3


def test_null_timezone_falls_back_to_platform_default():
    now = datetime(2026, 7, 2, 10, 0, tzinfo=UTC)
    fire = next_utc_fire("14:30", timezone=None, now=now)
    assert fire == datetime(2026, 7, 2, 14, 30, tzinfo=UTC)


def test_missed_window_is_not_backfilled():
    """A window already past fires tomorrow — never in the past."""
    now = datetime(2026, 7, 2, 15, 0, tzinfo=UTC)
    fire = next_utc_fire("14:30", timezone=None, now=now)
    assert fire == datetime(2026, 7, 3, 14, 30, tzinfo=UTC)
    assert fire > now


def test_dst_shift_moves_utc_keeps_local():
    """New York 09:00 is UTC-5 in winter and UTC-4 in summer — the human time holds."""
    winter = next_utc_fire(
        "09:00", timezone="America/New_York", now=datetime(2026, 1, 15, 5, 0, tzinfo=UTC)
    )
    summer = next_utc_fire(
        "09:00", timezone="America/New_York", now=datetime(2026, 6, 15, 5, 0, tzinfo=UTC)
    )
    assert winter.hour == 14
    assert summer.hour == 13
