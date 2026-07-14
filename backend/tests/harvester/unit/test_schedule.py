"""Due-ness math for the Harvester ticks: pure functions, injected clock (unit)."""

from datetime import UTC, datetime, timedelta

import pytest

from achilles.harvester.constants import SyncMode, SyncTrigger
from achilles.harvester.services import schedule
from achilles.infra.scheduler.cron import minute_of_week
from achilles.knowledge_store.models import PlatformSettings, Source

pytestmark = [pytest.mark.unit, pytest.mark.p1]

# Wednesday 2026-07-01 12:00 UTC → minute-of-week 2*1440 + 12*60 = 3600.
NOW = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


def _source(**kwargs: object) -> Source:
    defaults: dict[str, object] = {
        "name": "S",
        "connector_type": "jira",
        "state": "active",
        "scope_list": [],
        "content_filters": {},
    }
    return Source(**{**defaults, **kwargs})  # type: ignore[arg-type]


def _platform(**kwargs: object) -> PlatformSettings:
    defaults: dict[str, object] = {
        "id": 1,
        "timezone": "UTC",
        "sync_interval_minutes": 15,
        "reconcile_minute_of_week": 3600,
        "watchdog_silence_hours": 12,
        "curation_frequency": "daily",
        "curation_time": "04:00",
    }
    return PlatformSettings(**{**defaults, **kwargs})  # type: ignore[arg-type]


def test_minute_of_week_respects_timezone() -> None:
    assert minute_of_week(NOW, timezone="UTC") == 3600
    # Kyiv is UTC+3 in July: 15:00 local → 3*60 minutes later.
    assert minute_of_week(NOW, timezone="Europe/Kyiv") == 3600 + 180


def test_sync_due_interval_and_overrides() -> None:
    source = _source()
    platform = _platform()
    recent = NOW - timedelta(minutes=5)
    old = NOW - timedelta(minutes=30)

    assert (
        schedule.sync_due(
            source,
            platform,
            last_run_at=recent,
            last_success_at=recent,
            has_active_run=False,
            now=NOW,
        )
        is None
    )
    plan = schedule.sync_due(
        source, platform, last_run_at=old, last_success_at=old, has_active_run=False, now=NOW
    )
    assert plan is not None
    assert plan.mode == str(SyncMode.INCREMENTAL)
    assert plan.trigger == str(SyncTrigger.SCHEDULE)

    # Per-source override wins over the platform default.
    slow = _source(sync_interval=60)
    assert (
        schedule.sync_due(
            slow, platform, last_run_at=old, last_success_at=old, has_active_run=False, now=NOW
        )
        is None
    )


def test_sync_due_watchdog_escalation() -> None:
    old = NOW - timedelta(minutes=30)
    silent = NOW - timedelta(hours=13)
    plan = schedule.sync_due(
        _source(),
        _platform(),
        last_run_at=old,
        last_success_at=silent,
        has_active_run=False,
        now=NOW,
    )
    assert plan is not None
    assert plan.trigger == str(SyncTrigger.WATCHDOG)


def test_sync_due_skips_paused_and_busy() -> None:
    old = NOW - timedelta(hours=1)
    assert (
        schedule.sync_due(
            _source(state="paused"),
            _platform(),
            last_run_at=old,
            last_success_at=old,
            has_active_run=False,
            now=NOW,
        )
        is None
    )
    assert (
        schedule.sync_due(
            _source(),
            _platform(),
            last_run_at=old,
            last_success_at=old,
            has_active_run=True,
            now=NOW,
        )
        is None
    )


def test_sync_due_never_synced_starts_full() -> None:
    plan = schedule.sync_due(
        _source(),
        _platform(),
        last_run_at=None,
        last_success_at=None,
        has_active_run=False,
        now=NOW,
    )
    assert plan is not None
    assert plan.mode == str(SyncMode.FULL)


def test_reconcile_due_fires_at_and_after_the_window() -> None:
    source = _source()
    platform = _platform()  # window = 3600 = NOW's minute-of-week (Wed 12:00)

    # At the window: due.
    assert schedule.reconcile_due(
        source, platform, last_reconcile_at=None, has_active_run=False, now=NOW
    )
    # One minute past it, still uncovered: due — no longer gated on an exact
    # minute match (the old behaviour deferred a full week here).
    assert schedule.reconcile_due(
        source,
        platform,
        last_reconcile_at=None,
        has_active_run=False,
        now=NOW + timedelta(minutes=1),
    )
    # Covered this window (a run journalled at the window) → not due afterwards.
    assert not schedule.reconcile_due(
        source,
        platform,
        last_reconcile_at=NOW,
        has_active_run=False,
        now=NOW + timedelta(minutes=5),
    )
    # Before this week's window, last week already covered → not due yet.
    assert not schedule.reconcile_due(
        source,
        platform,
        last_reconcile_at=NOW - timedelta(days=7),
        has_active_run=False,
        now=NOW - timedelta(minutes=30),
    )


def test_reconcile_due_self_heals_a_missed_window_minute() -> None:
    # The regression this fix targets: the tick misses the exact window minute
    # (scheduler restart / jitter / a sync run holding the source) and the next
    # tick, minutes later, must still fire instead of waiting a whole week.
    source = _source()
    platform = _platform()  # weekly window at Wed 12:00
    assert schedule.reconcile_due(
        source,
        platform,
        last_reconcile_at=NOW - timedelta(days=7),  # covered last week, not this one
        has_active_run=False,
        now=NOW + timedelta(minutes=3),  # 12:03 — the 12:00 tick never ran
    )


def test_reconcile_due_per_source_window_override() -> None:
    platform = _platform()
    shifted = _source(reconcile_window=3601)  # Wed 12:01
    covered_last_week = NOW - timedelta(days=7) + timedelta(minutes=1)  # last Wed 12:01

    # This week's 12:01 not reached yet → not due.
    assert not schedule.reconcile_due(
        shifted, platform, last_reconcile_at=covered_last_week, has_active_run=False, now=NOW
    )
    # 12:01 reached → due.
    assert schedule.reconcile_due(
        shifted,
        platform,
        last_reconcile_at=covered_last_week,
        has_active_run=False,
        now=NOW + timedelta(minutes=1),
    )


def test_reconcile_due_honors_per_source_interval() -> None:
    # Biweekly override at the same weekly window (Wed 12:00 = NOW's mow 3600).
    source = _source(reconcile_interval=14, reconcile_window=3600)
    platform = _platform()

    # No prior sweep → runs at the window.
    assert schedule.reconcile_due(
        source, platform, last_reconcile_at=None, has_active_run=False, now=NOW
    )
    # A week on, the window matches again — but the 14-day cadence gates it out.
    assert not schedule.reconcile_due(
        source, platform, last_reconcile_at=NOW - timedelta(days=7), has_active_run=False, now=NOW
    )
    # Two weeks on, the cadence has elapsed → runs (tick jitter absorbed by slack).
    assert schedule.reconcile_due(
        source, platform, last_reconcile_at=NOW - timedelta(days=14), has_active_run=False, now=NOW
    )


def test_reconcile_due_sub_weekly_reads_window_as_minute_of_day() -> None:
    # Every 3 days: the weekday drops out, only the time-of-day (12:00) matches.
    source = _source(reconcile_interval=3, reconcile_window=3600)  # Wed 12:00
    platform = _platform()

    thursday_noon = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)
    monday_noon = thursday_noon - timedelta(days=3)  # 3 days before, at the same 12:00

    # A *different* weekday at 12:00 fires once the 3-day cadence has elapsed
    # (weekly matching would miss it — the weekday is irrelevant here).
    assert schedule.reconcile_due(
        source, platform, last_reconcile_at=monday_noon, has_active_run=False, now=thursday_noon
    )
    # Self-heals a missed minute-of-day tick within the day too.
    assert schedule.reconcile_due(
        source,
        platform,
        last_reconcile_at=monday_noon,
        has_active_run=False,
        now=thursday_noon + timedelta(minutes=5),
    )
    # The 3-day cadence still gates: two days is too soon.
    assert not schedule.reconcile_due(
        source,
        platform,
        last_reconcile_at=thursday_noon - timedelta(days=2),
        has_active_run=False,
        now=thursday_noon,
    )


def test_probe_due_daily_and_skips_disconnected() -> None:
    assert schedule.probe_due(_source(last_probe_at=None), now=NOW)
    assert schedule.probe_due(_source(last_probe_at=NOW - timedelta(hours=25)), now=NOW)
    assert not schedule.probe_due(_source(last_probe_at=NOW - timedelta(hours=1)), now=NOW)
    assert not schedule.probe_due(_source(state="disconnected", last_probe_at=None), now=NOW)
