"""Schedule → next UTC slot (execution.html#schedule).

Interval counts from the previous start; calendar resolves a naive HH:MM
against the owner's timezone at expansion time (same DST-stability contract
as the platform cron windows), then lands in next_run_at as UTC. A missed
slot is never backfilled — the next one simply comes.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from achilles.agent_engine.constants import CalendarCadence
from achilles.agent_engine.schemas import CalendarSchedule, IntervalSchedule, ScheduleSpec
from achilles.infra.scheduler.cron import next_utc_fire


def _next_interval(schedule: IntervalSchedule, *, now: datetime, base: datetime | None) -> datetime:
    slot = (base or now) + timedelta(hours=schedule.every_hours)
    # A stale anchor must not schedule into the past — fire on the next scan.
    return max(slot, now)


def _next_calendar(schedule: CalendarSchedule, *, tz: ZoneInfo, now: datetime) -> datetime:
    if schedule.cadence == CalendarCadence.DAILY:
        return next_utc_fire(schedule.time, timezone=tz.key, now=now)
    assert schedule.weekday is not None  # noqa: S101 — schema enforces weekly+weekday
    return next_utc_fire(schedule.time, timezone=tz.key, now=now, weekday=schedule.weekday)


def next_slot(
    schedule: ScheduleSpec, *, tz: ZoneInfo, now: datetime, base: datetime | None = None
) -> datetime:
    """The next UTC moment the schedule fires; `base` anchors an interval chain."""
    if isinstance(schedule, IntervalSchedule):
        return _next_interval(schedule, now=now, base=base)
    return _next_calendar(schedule, tz=tz, now=now)
