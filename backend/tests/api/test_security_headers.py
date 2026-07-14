"""Security headers on every response — protection.html station 2."""

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.api, pytest.mark.p1]

EXPECTED_COMMON = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Content-Security-Policy": (
        "default-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


async def test_common_headers_on_ok_response(client: AsyncClient):
    resp = await client.get("/api/v1/widgets")
    for name, value in EXPECTED_COMMON.items():
        assert resp.headers.get(name) == value, name
    assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"


async def test_common_headers_on_error_response(client: AsyncClient):
    resp = await client.get("/api/v1/definitely-missing")
    assert resp.status_code == 404
    for name, value in EXPECTED_COMMON.items():
        assert resp.headers.get(name) == value, name


async def test_token_routes_never_cache_never_refer(client: AsyncClient):
    resp = await client.get("/api/v1/auth/echo")
    assert resp.headers["Referrer-Policy"] == "no-referrer"
    assert resp.headers["Cache-Control"] == "no-store"
