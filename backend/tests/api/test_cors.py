"""CORS: exact-origin whitelist with credentials — protection.html#cors."""

import pytest
from httpx import AsyncClient

from tests.conftest import TEST_ORIGIN

pytestmark = [pytest.mark.api, pytest.mark.p1]


async def test_preflight_allowed_origin(client: AsyncClient):
    resp = await client.options(
        "/api/v1/widgets",
        headers={
            "Origin": TEST_ORIGIN,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["Access-Control-Allow-Origin"] == TEST_ORIGIN
    assert resp.headers["Access-Control-Allow-Credentials"] == "true"
    assert resp.headers["Access-Control-Max-Age"] == "3600"


async def test_preflight_disallowed_origin(client: AsyncClient):
    resp = await client.options(
        "/api/v1/widgets",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert "Access-Control-Allow-Origin" not in resp.headers


async def test_simple_request_exposes_contract_headers(client: AsyncClient):
    resp = await client.get("/api/v1/widgets", headers={"Origin": TEST_ORIGIN})
    assert resp.headers["Access-Control-Allow-Origin"] == TEST_ORIGIN
    exposed = resp.headers.get("Access-Control-Expose-Headers", "")
    for header in ("Retry-After", "X-RateLimit-Remaining", "X-Request-Id"):
        assert header in exposed
