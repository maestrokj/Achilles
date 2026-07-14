"""Cross-router invariants, parametrized over app.routes — new endpoints inherit them."""

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import AsyncClient

pytestmark = [pytest.mark.api, pytest.mark.p1]

# Routes that legitimately answer without identity. Everything else under /api/v1
# must refuse anonymous calls — a new endpoint cannot slip past unnoticed.
PUBLIC_V1_PATHS = {
    "/api/v1/widgets",
    "/api/v1/widgets/echo",
    "/api/v1/limited",
    "/api/v1/auth/echo",
    "/api/v1/auth/setup",
    "/api/v1/auth/login",
    "/api/v1/auth/password/forgot",
    "/api/v1/auth/password/reset",
    "/api/v1/platform/branding",  # the login screen paints the org identity pre-auth
    "/api/v1/slack/events",  # anonymous by design: the gate is the Slack signature
    "/api/v1/telegram/webhook",  # anonymous by design: the gate is the secret token
}


def _v1_routes(app: FastAPI) -> list[APIRoute]:
    return [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/api/v1")
    ]


async def test_datetimes_serialize_as_utc_z(client: AsyncClient):
    resp = await client.get("/api/v1/widgets")
    created = resp.json()["items"][0]["created_at"]
    assert created.endswith("Z"), f"expected Z suffix, got {created!r}"
    assert "+00:00" not in created


async def test_version_lives_in_path(client: AsyncClient):
    assert (await client.get("/api/v1/widgets")).status_code == 200
    assert (await client.get("/api/widgets")).status_code == 404


async def test_infra_tier_is_unversioned(client: AsyncClient):
    health = await client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] == "healthy"
    version = await client.get("/api/version")
    assert version.status_code == 200


async def test_405_uses_the_envelope(client: AsyncClient):
    resp = await client.delete("/api/v1/widgets")
    assert resp.status_code == 405
    body = resp.json()
    assert body["code"] == "METHOD_NOT_ALLOWED"
    assert body["request_id"]


async def test_no_naked_routes(app: FastAPI, client: AsyncClient):
    """Every non-public /api/v1 route must refuse anonymous requests with 401."""
    for route in _v1_routes(app):
        if route.path in PUBLIC_V1_PATHS or "{" in route.path:
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            resp = await client.request(method, route.path)
            assert resp.status_code == 401, (
                f"{method} {route.path} answered {resp.status_code} without identity"
            )
