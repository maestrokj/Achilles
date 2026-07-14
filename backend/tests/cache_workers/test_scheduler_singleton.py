"""Scheduler singleton contract: cron lives on one dedicated service (unit).

The N-replica race itself is prevented by topology (compose pins 1 replica) and
by the Postgres uniqueness lock (test_uniqueness_reaping::double_tick); here the
wiring contract is pinned so a refactor cannot silently break it.
"""

import pytest
from saq import Worker

from achilles.infra.scheduler.settings import SCHEDULER_QUEUE, settings
from achilles.infra.worker.base import Lane

pytestmark = [pytest.mark.unit]


def test_scheduler_has_its_own_queue():
    lane_names = {lane.value for lane in Lane}
    assert settings["queue"].name == SCHEDULER_QUEUE
    assert SCHEDULER_QUEUE not in lane_names, "the singleton publishes, workers drain"


def test_singleton_crons_are_registered():
    cron_names = {cron.function.__name__ for cron in settings["cron_jobs"]}
    assert cron_names == {
        "reap_stale_runs",
        "backup_tick",
        "curation_tick",
        "sync_tick",
        "reconcile_tick",
        "health_tick",
        "agents_tick",
        "notifications_tick",
    }


def test_scheduler_is_single_threaded():
    assert settings["concurrency"] == 1


def test_settings_are_consumable_by_saq():
    worker = Worker(**settings)
    assert worker.queue.name == SCHEDULER_QUEUE
