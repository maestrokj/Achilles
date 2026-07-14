"""Window due-ness: naive HH:MM window + daily/weekly cadence → concrete UTC moment.

Shared by the backup and Curation Pass schedules (both are org-local windows in
platform-owned settings). Mirrors the cron-window convention
(cache-workers/scheduling.html#cron): a missed window is never backfilled —
only the latest fire counts. weekday is ISO-style: 0 = Monday … 6 = Sunday.
"""

from datetime import UTC, datetime, time, timedelta
from typing import NamedTuple, Self
from zoneinfo import ZoneInfo

from achilles.api.problems import field_validation_error
from achilles.infra.scheduler.cron import DEFAULT_ORG_TIMEZONE
from achilles.knowledge_store.constants import CadenceFrequency
from achilles.knowledge_store.models import BackupSettings, PlatformSettings


class WindowCadence(NamedTuple):
    """The daily/weekly + HH:MM window shape (backup_settings, curation_*)."""

    frequency: str
    weekday: int | None
    time: str

    @classmethod
    def for_curation(cls, row: PlatformSettings) -> Self:
        return cls(
            frequency=row.curation_frequency, weekday=row.curation_weekday, time=row.curation_time
        )

    @classmethod
    def for_backup(cls, row: BackupSettings) -> Self:
        return cls(frequency=row.frequency, weekday=row.weekday, time=row.time)


def normalize_cadence(frequency: str, weekday: int | None, *, field: str) -> int | None:
    """The one merged-row cadence rule: daily ⇒ weekday NULL, weekly ⇒ weekday required.

    Returns the normalized weekday; ``field`` names the offending column in the 422.
    """
    if frequency == str(CadenceFrequency.DAILY):
        return None
    if weekday is None:
        raise field_validation_error(field, "weekly cadence needs a weekday")
    return weekday


def last_fire(
    cadence: WindowCadence, *, timezone: str = DEFAULT_ORG_TIMEZONE, now: datetime
) -> datetime:
    """The most recent UTC moment the configured window fired (≤ now)."""
    hour, minute = (int(part) for part in cadence.time.split(":"))
    tz = ZoneInfo(timezone)
    local_now = now.astimezone(tz)

    candidate_date = local_now.date()
    period = timedelta(days=1)
    if cadence.frequency == str(CadenceFrequency.WEEKLY):
        weekday = cadence.weekday or 0
        candidate_date -= timedelta(days=(candidate_date.weekday() - weekday) % 7)
        period = timedelta(days=7)

    candidate = datetime.combine(candidate_date, time(hour, minute), tzinfo=tz)
    if candidate > local_now:
        candidate -= period
    return candidate.astimezone(UTC)


def next_fire(
    cadence: WindowCadence, *, timezone: str = DEFAULT_ORG_TIMEZONE, now: datetime
) -> datetime:
    """The next UTC moment the configured window fires (> now) — for Admin display.

    A wall-clock step, not a fixed UTC delta: across a DST transition the window
    stays at the configured local HH:MM.
    """
    hour, minute = (int(part) for part in cadence.time.split(":"))
    tz = ZoneInfo(timezone)
    period = timedelta(days=7 if cadence.frequency == str(CadenceFrequency.WEEKLY) else 1)
    next_date = last_fire(cadence, timezone=timezone, now=now).astimezone(tz).date() + period
    return datetime.combine(next_date, time(hour, minute), tzinfo=tz).astimezone(UTC)


def is_due(
    cadence: WindowCadence,
    *,
    last_started_at: datetime | None,
    timezone: str = DEFAULT_ORG_TIMEZONE,
    now: datetime,
) -> datetime | None:
    """The fire moment not yet covered by a run, or None when covered."""
    fire = last_fire(cadence, timezone=timezone, now=now)
    if last_started_at is None or last_started_at < fire:
        return fire
    return None
