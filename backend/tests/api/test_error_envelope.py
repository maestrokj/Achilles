"""The one refusal shape: RFC 9457 problem+json on every router."""

import json

import pytest
from httpx import AsyncClient
from starlette.requests import Request

from achilles.api.problems import (
    CODE_INTERNAL_ERROR,
    PROBLEM_CONTENT_TYPE,
    _handle_unhandled,  # pyright: ignore[reportPrivateUsage] — the 500 path is only reachable this way
)

pytestmark = [pytest.mark.api, pytest.mark.p1]

ENVELOPE_FIELDS = {"type", "title", "status", "detail", "code", "request_id"}


async def test_404_is_problem_json(client: AsyncClient):
    resp = await client.get("/api/v1/definitely-missing")
    assert resp.status_code == 404
    assert resp.headers["content-type"] == PROBLEM_CONTENT_TYPE
    body = resp.json()
    assert body.keys() >= ENVELOPE_FIELDS
    assert body["code"] == "NOT_FOUND"
    assert body["type"] == "/errors/not-found"
    assert body["status"] == 404


async def test_request_id_matches_header(client: AsyncClient):
    resp = await client.get("/api/v1/definitely-missing")
    assert resp.json()["request_id"] == resp.headers["X-Request-Id"]
    assert resp.headers["X-Request-Id"].startswith("req_")


async def test_inbound_request_id_echoed(client: AsyncClient):
    resp = await client.get("/api/v1/widgets", headers={"X-Request-Id": "req_client-42"})
    assert resp.headers["X-Request-Id"] == "req_client-42"


async def test_unsafe_inbound_request_id_replaced(client: AsyncClient):
    resp = await client.get("/api/v1/widgets", headers={"X-Request-Id": "bad id\twith spaces"})
    assert resp.headers["X-Request-Id"].startswith("req_")
    assert resp.headers["X-Request-Id"] != "bad id\twith spaces"


async def test_422_wraps_framework_validation(client: AsyncClient):
    resp = await client.post("/api/v1/widgets/echo", json={"name": "x", "count": "not-an-int"})
    assert resp.status_code == 422
    assert resp.headers["content-type"] == PROBLEM_CONTENT_TYPE
    body = resp.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["errors"], "422 must carry the errors array"
    fields = {e["field"] for e in body["errors"]}
    assert "count" in fields
    assert all({"field", "message"} <= e.keys() for e in body["errors"])


async def test_429_carries_retry_after(client: AsyncClient):
    responses = [await client.get("/api/v1/limited") for _ in range(3)]
    assert [r.status_code for r in responses[:2]] == [200, 200]
    assert all("X-RateLimit-Remaining" in r.headers for r in responses[:2])
    refused = responses[2]
    assert refused.status_code == 429
    body = refused.json()
    assert body["code"] == "RATE_LIMITED"
    assert isinstance(body["retry_after"], int)
    assert body["retry_after"] > 0
    assert refused.headers["Retry-After"] == str(body["retry_after"])


async def test_unhandled_exception_shape():
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/x",
        "headers": [],
        "query_string": b"",
        "state": {"request_id": "req_test"},
    }
    response = await _handle_unhandled(Request(scope), RuntimeError("boom"))
    assert response.status_code == 500
    assert response.media_type == PROBLEM_CONTENT_TYPE
    body = json.loads(bytes(response.body))
    assert body["code"] == CODE_INTERNAL_ERROR
    assert body["request_id"] == "req_test"
    assert "boom" not in body["detail"], "internals must never leak"
