"""Rate-limit budget clamp parsing (_safe_rps): vendor header spellings (unit)."""

import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from achilles.harvester.pipeline.throttle import _safe_rps

pytestmark = [pytest.mark.unit, pytest.mark.p1]

_WINDOW_SECONDS = 10
_REMAINING = 30


def test_gitlab_plain_headers_engage_the_clamp() -> None:
    headers = httpx.Headers(
        {
            "RateLimit-Remaining": str(_REMAINING),
            "RateLimit-Reset": str(time.time() + _WINDOW_SECONDS),
        }
    )

    safe = _safe_rps(headers)

    assert safe is not None
    assert safe == pytest.approx(_REMAINING / _WINDOW_SECONDS, rel=0.2)


def test_atlassian_iso_reset_engages_the_clamp() -> None:
    reset_at = datetime.now(UTC) + timedelta(seconds=_WINDOW_SECONDS)
    headers = httpx.Headers(
        {"X-RateLimit-Remaining": str(_REMAINING), "X-RateLimit-Reset": reset_at.isoformat()}
    )

    safe = _safe_rps(headers)

    assert safe is not None
    assert safe == pytest.approx(_REMAINING / _WINDOW_SECONDS, rel=0.2)


def test_naive_iso_reset_is_treated_as_utc() -> None:
    reset_at = datetime.now(UTC) + timedelta(seconds=_WINDOW_SECONDS)
    headers = httpx.Headers(
        {
            "X-RateLimit-Remaining": str(_REMAINING),
            "X-RateLimit-Reset": reset_at.replace(tzinfo=None).isoformat(),
        }
    )

    safe = _safe_rps(headers)

    assert safe is not None
    assert safe == pytest.approx(_REMAINING / _WINDOW_SECONDS, rel=0.2)


def test_x_prefixed_headers_win_over_plain() -> None:
    now = time.time()
    headers = httpx.Headers(
        {
            "X-RateLimit-Remaining": "100",
            "X-RateLimit-Reset": str(now + _WINDOW_SECONDS),
            "RateLimit-Remaining": "1",
            "RateLimit-Reset": str(now + 1000),
        }
    )

    safe = _safe_rps(headers)

    assert safe is not None
    assert safe == pytest.approx(100 / _WINDOW_SECONDS, rel=0.2)


def test_garbage_reset_yields_none() -> None:
    headers = httpx.Headers({"X-RateLimit-Remaining": str(_REMAINING), "X-RateLimit-Reset": "soon"})

    assert _safe_rps(headers) is None


def test_expired_window_yields_none() -> None:
    headers = httpx.Headers(
        {
            "X-RateLimit-Remaining": str(_REMAINING),
            "X-RateLimit-Reset": str(time.time() - 1),
        }
    )

    assert _safe_rps(headers) is None
