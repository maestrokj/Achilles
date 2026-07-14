"""Shared HTTP core for httpx-based connectors (reliability.html#retry, #rate).

One request path for every connector: throttle gate → request → feedback →
classify. Transient errors retry in-memory with full-jitter backoff (short
pauses hold the worker slot; long `Retry-After` pauses are the throttle's
business — it parks the whole scope). Permanent errors surface as
SourceItemError carrying the DLQ reason.
"""

import asyncio
import logging
import random
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Protocol, cast

import httpx

from achilles.harvester.constants import DlqReason, ErrorClass

logger = logging.getLogger(__name__)

# In-memory retry envelope: 5 attempts, base 1 s x2, cap 60 s, full jitter.
# The cap stays under the 90 s heartbeat-zombie threshold on purpose — a run
# sleeping out a backoff must keep beating before the reaper's anchor expires.
HTTP_MAX_ATTEMPTS = 5
HTTP_BACKOFF_BASE_SECONDS = 1.0
HTTP_BACKOFF_CAP_SECONDS = 60.0
HTTP_TIMEOUT_SECONDS = 30.0

_TRANSIENT_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
_PERMISSION_STATUSES = frozenset({401, 403})
_NOT_FOUND_STATUSES = frozenset({404, 410})


class Throttle(Protocol):
    """Pace gate; the real implementation (pipeline/throttle.py) is Redis-backed."""

    async def acquire(self, cost: int = 1) -> None: ...

    async def feedback(self, status_code: int, headers: httpx.Headers) -> None: ...


class SourceItemError(Exception):
    """Permanent per-item failure → dead_letters row, the run keeps going."""

    def __init__(self, reason: DlqReason, detail: str = "") -> None:
        super().__init__(detail or str(reason))
        self.reason = reason
        self.detail = detail


class SourceUnavailableError(Exception):
    """Transient budget exhausted — the run (not the item) fails as transient."""


def classify_error(status_code: int, headers: httpx.Headers) -> ErrorClass:
    """Default HTTP → error-class mapping; connectors override via manifest hook."""
    del headers
    if status_code in _TRANSIENT_STATUSES:
        return ErrorClass.TRANSIENT
    return ErrorClass.PERMANENT


def dlq_reason(status_code: int) -> DlqReason:
    if status_code in _PERMISSION_STATUSES:
        return DlqReason.PERMISSION
    if status_code in _NOT_FOUND_STATUSES:
        return DlqReason.NOT_FOUND
    if status_code == 429:
        return DlqReason.RATE_LIMITED
    return DlqReason.MALFORMED


def retry_after_seconds(headers: httpx.Headers, *, now: datetime | None = None) -> float | None:
    """Parse Retry-After (seconds or HTTP-date); None when absent/unparsable."""
    value = headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        target = parsedate_to_datetime(value)
    except TypeError, ValueError:
        return None
    return max(0.0, (target - (now or datetime.now(UTC))).total_seconds())


def _backoff_seconds(attempt: int) -> float:
    ceiling = min(HTTP_BACKOFF_BASE_SECONDS * 2**attempt, HTTP_BACKOFF_CAP_SECONDS)
    return random.random() * ceiling  # noqa: S311 — jitter, not crypto


class SourceHttpClient:
    """httpx wrapper with the platform retry/throttle/classification contract."""

    def __init__(
        self,
        *,
        base_url: str = "",
        headers: dict[str, str] | None = None,
        throttle: Throttle | None = None,
        classify: Callable[[int, httpx.Headers], ErrorClass] = classify_error,
        request_cost: int = 1,
        timeout: float = HTTP_TIMEOUT_SECONDS,
    ) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, headers=headers or {}, timeout=timeout)
        self._throttle = throttle
        self._classify = classify
        self._request_cost = request_cost

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_json(
        self, url: str, *, params: dict[str, str | int] | None = None
    ) -> dict[str, object]:
        response = await self.request("GET", url, params=params)
        return cast("dict[str, object]", response.json())

    async def get_json_list(
        self, url: str, *, params: dict[str, str | int] | None = None
    ) -> list[dict[str, object]]:
        response = await self.request("GET", url, params=params)
        return cast("list[dict[str, object]]", response.json())

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str | int] | None = None,
    ) -> httpx.Response:
        last_status = 0
        for attempt in range(HTTP_MAX_ATTEMPTS):
            if self._throttle is not None:
                await self._throttle.acquire(self._request_cost)
            try:
                response = await self._client.request(method, url, params=params)
            except httpx.TransportError as exc:
                logger.debug("transport error on %s %s: %s", method, url, exc)
                await asyncio.sleep(_backoff_seconds(attempt))
                continue
            if self._throttle is not None:
                await self._throttle.feedback(response.status_code, response.headers)
            if response.is_success:
                return response
            last_status = response.status_code
            if self._classify(response.status_code, response.headers) is ErrorClass.PERMANENT:
                raise SourceItemError(
                    dlq_reason(response.status_code),
                    f"{method} {url} → {response.status_code}",
                )
            # Retry-After overrides the computed backoff (reliability.html#retry).
            pause = retry_after_seconds(response.headers)
            await asyncio.sleep(pause if pause is not None else _backoff_seconds(attempt))
        msg = f"{method} {url}: transient budget exhausted (last status {last_status})"
        raise SourceUnavailableError(msg)
