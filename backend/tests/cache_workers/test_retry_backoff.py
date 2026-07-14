"""Lifecycle retry classification and backoff — cache-workers tests (unit)."""

import pytest

from achilles.infra.lifecycle import (
    BACKOFF_CAP,
    PermanentJobError,
    backoff_delay,
    is_transient,
)

pytestmark = [pytest.mark.unit]


def test_permanent_errors_do_not_retry():
    assert not is_transient(PermanentJobError("revoked token"))


def test_everything_else_is_transient():
    assert is_transient(TimeoutError())
    assert is_transient(ConnectionError())
    assert is_transient(RuntimeError("flaky"))


def test_backoff_grows_but_caps():
    for attempt in range(12):
        delay = backoff_delay(attempt)
        assert 0 <= delay.total_seconds() <= BACKOFF_CAP.total_seconds()


def test_backoff_is_jittered():
    delays = {backoff_delay(8).total_seconds() for _ in range(20)}
    assert len(delays) > 1, "full jitter: identical retries must not stampede"
