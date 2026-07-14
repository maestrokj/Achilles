"""Due-ness math for the Harvester cron ticks (sync-modes.html#scheduling).

Pure functions taking `now` — the ticks stay thin publish-only bodies, the
window logic tests without a clock. Defaults live in platform_settings; a
source overrides via its own columns, NULL = inherit.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta

from achilles.harvester.constants import SyncMode, SyncTrigger
from achilles.infra.scheduler.cron import safe_zone
from achilles.knowledge_store.constants import SourceState
from achilles.knowledge_store.models import PlatformSettings, Source

# Reconcile is due while `now` sits past the last window opening and no run has
# covered it yet (mirrors the KS window pattern: only the latest fire counts, a
# missed tick self-heals on the next one). The run-lock plus job-id dedup make a
# same-window double fire harmless; this repeat guard keeps the journal clean.
RECONCILE_REPEAT_GUARD = timedelta(days=1)
# A cadence guard is measured tick-to-tick, so it carries the scheduler's
# minute-level jitter (a run journalled at 03:00:07 vs a window matched at
# 03:00:00 the next period). Shave an hour so an interval never rounds up to
# its next window occurrence — far below the smallest cadence (1 day), far
# above any real jitter.
RECONCILE_GUARD_SLACK = timedelta(hours=1)
PROBE_INTERVAL = timedelta(hours=24)  # the daily light probe (sources.html)

MINUTES_PER_DAY = 1440
DAYS_PER_WEEK = 7


@dataclass(frozen=True, slots=True)
class DuePlan:
    mode: str
    trigger: str


def sync_due(
    source: Source,
    platform: PlatformSettings,
    *,
    last_run_at: datetime | None,
    last_success_at: datetime | None,
    has_active_run: bool,
    now: datetime | None = None,
) -> DuePlan | None:
    """Incremental cadence + the watchdog silence escalation, per source."""
    now = now or datetime.now(UTC)
    if source.state != str(SourceState.ACTIVE) or has_active_run:
        return None
    if last_run_at is None:
        # Never synced (source predates the auto-Full path) — start from zero.
        return DuePlan(mode=str(SyncMode.FULL), trigger=str(SyncTrigger.SCHEDULE))
    interval = timedelta(minutes=source.sync_interval or platform.sync_interval_minutes)
    if now - last_run_at < interval:
        return None
    silence = timedelta(hours=platform.watchdog_silence_hours)
    if last_success_at is None or now - last_success_at >= silence:
        return DuePlan(mode=str(SyncMode.INCREMENTAL), trigger=str(SyncTrigger.WATCHDOG))
    return DuePlan(mode=str(SyncMode.INCREMENTAL), trigger=str(SyncTrigger.SCHEDULE))


def _last_reconcile_fire(
    window: int, *, sub_weekly: bool, timezone: str | None, now: datetime
) -> datetime:
    """The most recent UTC moment (≤ now) the reconcile window opened.

    A wall-clock step in the org timezone (DST-stable, like ``minute_of_week``):
    weekly windows land on their weekday, sub-weekly ones repeat every day.
    """
    tz = safe_zone(timezone)
    local_now = now.astimezone(tz)
    if sub_weekly:  # minute-of-day cadence — the weekday drops out
        hour, minute = divmod(window % MINUTES_PER_DAY, 60)
        candidate = datetime.combine(local_now.date(), time(hour, minute), tzinfo=tz)
        if candidate > local_now:
            candidate -= timedelta(days=1)
    else:  # minute-of-week cadence — weekday + time
        target_weekday, minute_of_day = divmod(window, MINUTES_PER_DAY)
        hour, minute = divmod(minute_of_day, 60)
        candidate_date = local_now.date() - timedelta(
            days=(local_now.weekday() - target_weekday) % DAYS_PER_WEEK
        )
        candidate = datetime.combine(candidate_date, time(hour, minute), tzinfo=tz)
        if candidate > local_now:
            candidate -= timedelta(days=DAYS_PER_WEEK)
    return candidate.astimezone(UTC)


def reconcile_due(
    source: Source,
    platform: PlatformSettings,
    *,
    last_reconcile_at: datetime | None,
    has_active_run: bool,
    now: datetime | None = None,
) -> bool:
    """Full sweep at the source's window, gated by its cadence.

    Two dials (data-model.html): `reconcile_interval` (days — how often; NULL
    inherits the weekly global default) and `reconcile_window` (when). A sub-weekly
    cadence can't sit in a single weekly slot, so its window is read as a
    minute-of-day (mod 1440) — the weekday drops out; a weekly-or-slower cadence
    keeps the full minute-of-week (weekday + time).

    Due-ness is "window opened and no run has covered it since", not an exact
    minute match — a tick that misses the window minute (scheduler restart,
    jitter, a sync run holding the source that minute) still fires on the next
    tick, instead of silently deferring a full period.
    """
    now = now or datetime.now(UTC)
    if source.state != str(SourceState.ACTIVE) or has_active_run:
        return False
    window = (
        source.reconcile_window
        if source.reconcile_window is not None
        else platform.reconcile_minute_of_week
    )
    interval_days = source.reconcile_interval
    sub_weekly = interval_days is not None and interval_days < DAYS_PER_WEEK
    fire = _last_reconcile_fire(window, sub_weekly=sub_weekly, timezone=platform.timezone, now=now)
    if last_reconcile_at is not None and last_reconcile_at >= fire:
        return False  # this window already covered
    # The sub-weekly window opens daily, so the days-interval is what rate-limits
    # it; the weekly window opens once a period, so the one-day guard only keeps a
    # double-fire out of the journal.
    guard = (
        timedelta(days=interval_days) - RECONCILE_GUARD_SLACK
        if interval_days is not None
        else RECONCILE_REPEAT_GUARD
    )
    return last_reconcile_at is None or now - last_reconcile_at >= guard


def probe_due(source: Source, *, now: datetime | None = None) -> bool:
    """The daily light probe; a paused source is still probed (health is health)."""
    now = now or datetime.now(UTC)
    if source.state == str(SourceState.DISCONNECTED):
        return False
    return source.last_probe_at is None or now - source.last_probe_at >= PROBE_INTERVAL
