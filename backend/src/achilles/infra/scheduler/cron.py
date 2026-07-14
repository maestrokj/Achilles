"""Cron-window expansion: naive org-time HH:MM → concrete UTC fire moment.

Design: cache-workers/_workzone/scheduling.html#cron. Windows are stored naive
in the org timezone; the scheduler expands them to UTC at fire time, so a DST
shift moves the UTC moment while the human-facing time stays put. A missed
window is never backfilled — the next one simply comes.
"""

import logging
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# The org timezone lives in platform_settings.timezone (seeded 'UTC'; edited
# on the Admin "Platform" screen) — ticks read it and pass it in. This is
# the fallback for callers with no settings row at hand.
DEFAULT_ORG_TIMEZONE = "UTC"


def safe_zone(name: str | None, fallback: ZoneInfo | None = None) -> ZoneInfo:
    """A stored IANA name that no longer resolves must not kill a scheduler tick."""
    if name:
        try:
            return ZoneInfo(name)
        except KeyError, ValueError:
            # Not silent: the misconfiguration must be visible in the logs —
            # every window quietly firing in UTC is much harder to diagnose.
            logger.warning("timezone %r does not resolve — falling back", name)
    return fallback or ZoneInfo(DEFAULT_ORG_TIMEZONE)


def minute_of_week(now: datetime, *, timezone: str | None) -> int:
    """Current minute-of-week in the org timezone; Monday 00:00 = 0.

    Weekly windows (Harvester reconciliation) are stored as a plain minute
    number without a zone; the tick compares against this expansion — the same
    DST-stability contract as next_utc_fire.
    """
    local = now.astimezone(safe_zone(timezone))
    return local.weekday() * 1440 + local.hour * 60 + local.minute


def next_utc_fire(
    window: str, *, timezone: str | None, now: datetime, weekday: int | None = None
) -> datetime:
    """The next UTC moment when the naive `HH:MM` window fires in the given tz.

    With `weekday` (Monday=0) the window is weekly — the candidate lands on
    that day and a passed moment rolls a week, not a day.
    """
    hour, minute = (int(part) for part in window.split(":"))
    tz = safe_zone(timezone)

    local_now = now.astimezone(tz)
    target = local_now.date()
    roll = timedelta(days=1)
    if weekday is not None:
        target += timedelta(days=(weekday - local_now.weekday()) % 7)
        roll = timedelta(days=7)
    candidate = datetime.combine(target, time(hour, minute), tzinfo=tz)
    if candidate <= local_now:
        candidate = datetime.combine(target + roll, time(hour, minute), tzinfo=tz)
    return candidate.astimezone(UTC)
