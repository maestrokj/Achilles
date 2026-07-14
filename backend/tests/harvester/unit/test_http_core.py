"""SourceHttpClient: retry envelope, classification, Retry-After (unit, respx)."""

import httpx
import pytest
import respx

from achilles.harvester.connectors import http as http_core
from achilles.harvester.connectors.http import (
    SourceHttpClient,
    SourceItemError,
    SourceUnavailableError,
    classify_error,
    dlq_reason,
    retry_after_seconds,
)
from achilles.harvester.constants import DlqReason, ErrorClass

pytestmark = [pytest.mark.unit, pytest.mark.p1]

BASE = "https://source.test"


def _zero_backoff(_attempt: int) -> float:
    return 0.0


@pytest.fixture(autouse=True)
def no_backoff_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(http_core, "_backoff_seconds", _zero_backoff)


@respx.mock
async def test_transient_retries_until_success() -> None:
    route = respx.get(f"{BASE}/items").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(429),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    client = SourceHttpClient(base_url=BASE)
    payload = await client.get_json("/items")
    await client.aclose()

    assert payload == {"ok": True}
    assert route.call_count == 3


@respx.mock
async def test_permanent_fails_immediately_with_reason() -> None:
    route = respx.get(f"{BASE}/items/1").mock(return_value=httpx.Response(403))
    client = SourceHttpClient(base_url=BASE)

    with pytest.raises(SourceItemError) as exc_info:
        await client.request("GET", "/items/1")
    await client.aclose()

    assert exc_info.value.reason == DlqReason.PERMISSION
    assert route.call_count == 1  # no retries on permanent


@respx.mock
async def test_transient_budget_exhausts() -> None:
    route = respx.get(f"{BASE}/items").mock(return_value=httpx.Response(503))
    client = SourceHttpClient(base_url=BASE)

    with pytest.raises(SourceUnavailableError):
        await client.request("GET", "/items")
    await client.aclose()

    assert route.call_count == http_core.HTTP_MAX_ATTEMPTS


@respx.mock
async def test_classification_override() -> None:
    """GitLab-style: 403 with rate-limit headers is transient, not permanent."""

    def forgiving(status_code: int, headers: httpx.Headers) -> ErrorClass:
        if status_code == 403 and "RateLimit-Remaining" in headers:
            return ErrorClass.TRANSIENT
        return classify_error(status_code, headers)

    route = respx.get(f"{BASE}/x").mock(
        side_effect=[
            httpx.Response(403, headers={"RateLimit-Remaining": "0"}),
            httpx.Response(200, json={}),
        ]
    )
    client = SourceHttpClient(base_url=BASE, classify=forgiving)
    response = await client.request("GET", "/x")
    await client.aclose()

    assert response.status_code == 200
    assert route.call_count == 2


@respx.mock
async def test_retry_after_is_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []

    async def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(http_core.asyncio, "sleep", record_sleep)
    respx.get(f"{BASE}/x").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, json={}),
        ]
    )
    client = SourceHttpClient(base_url=BASE)
    await client.request("GET", "/x")
    await client.aclose()

    assert sleeps == [7.0]


def test_dlq_reason_mapping() -> None:
    assert dlq_reason(401) == DlqReason.PERMISSION
    assert dlq_reason(404) == DlqReason.NOT_FOUND
    assert dlq_reason(410) == DlqReason.NOT_FOUND
    assert dlq_reason(429) == DlqReason.RATE_LIMITED
    assert dlq_reason(422) == DlqReason.MALFORMED


def test_retry_after_parses_seconds_and_ignores_garbage() -> None:
    assert retry_after_seconds(httpx.Headers({"Retry-After": "12"})) == 12.0
    assert retry_after_seconds(httpx.Headers({"Retry-After": "junk"})) is None
    assert retry_after_seconds(httpx.Headers({})) is None
