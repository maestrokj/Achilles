"""GET/PATCH /admin/settings + the public branding read (admin-panel tests.html)."""

import jwt as pyjwt
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories.admin import set_platform_settings
from tests.factories.users import DEFAULT_PASSWORD, create_user

pytestmark = [pytest.mark.api, pytest.mark.p1]

URL = "/api/v1/admin/settings"
BRANDING_URL = "/api/v1/platform/branding"


async def test_get_answers_the_seeded_defaults(client: AsyncClient, as_owner: None):
    resp = await client.get(URL)
    assert resp.status_code == 200
    body = resp.json()
    assert body["org_name"] == "Achilles"
    assert body["locale"] == "ru"
    assert body["date_format"] == "DD.MM.YYYY"
    assert body["accent_color"] == "#6366f1"
    assert body["maintenance_mode"] is False
    assert body["mcp_enabled"] is True
    assert body["curation_frequency"] == "daily"
    assert body["smtp_configured"] is False  # Email arrives in stage 9


async def test_partial_patch_touches_only_sent_fields(client: AsyncClient, as_owner: None):
    resp = await client.patch(URL, json={"org_name": "Acme"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["org_name"] == "Acme"
    assert body["locale"] == "ru", "untouched fields keep their values"


async def test_patch_null_clears_a_nullable_field(client: AsyncClient, as_owner: None):
    await client.patch(URL, json={"org_logo_url": "https://acme.example/logo.svg"})
    cleared = await client.patch(URL, json={"org_logo_url": None})
    assert cleared.status_code == 200
    assert cleared.json()["org_logo_url"] is None


async def test_patch_null_on_a_non_nullable_field_is_422(client: AsyncClient, as_owner: None):
    resp = await client.patch(URL, json={"org_name": None})
    assert resp.status_code == 422
    assert resp.json()["errors"][0]["field"] == "org_name"


async def test_weekly_cadence_requires_a_weekday(client: AsyncClient, as_owner: None):
    resp = await client.patch(URL, json={"curation_frequency": "weekly"})
    assert resp.status_code == 422
    assert resp.json()["errors"][0]["field"] == "curation_weekday"

    ok = await client.patch(URL, json={"curation_frequency": "weekly", "curation_weekday": 6})
    assert ok.status_code == 200


async def test_daily_cadence_nulls_the_weekday(client: AsyncClient, as_owner: None):
    await client.patch(URL, json={"curation_frequency": "weekly", "curation_weekday": 2})
    back = await client.patch(URL, json={"curation_frequency": "daily"})
    assert back.status_code == 200
    assert back.json()["curation_weekday"] is None


async def test_budget_alert_requires_a_budget(client: AsyncClient, as_owner: None):
    resp = await client.patch(URL, json={"ai_budget_alert_enabled": True})
    assert resp.status_code == 422

    ok = await client.patch(
        URL, json={"ai_budget_alert_enabled": True, "ai_monthly_budget": "500.00"}
    )
    assert ok.status_code == 200


async def test_ttls_must_nest(client: AsyncClient, as_owner: None):
    """Access ⊆ refresh ⊆ absolute — an inverted bound would silently truncate the inner one."""
    inverted_access = await client.patch(
        URL, json={"access_token_ttl": 3600, "refresh_token_ttl": 60}
    )
    assert inverted_access.status_code == 422
    assert inverted_access.json()["errors"][0]["field"] == "access_token_ttl"

    inverted_refresh = await client.patch(URL, json={"refresh_token_ttl": 86_400 * 200})
    assert inverted_refresh.status_code == 422
    assert inverted_refresh.json()["errors"][0]["field"] == "refresh_token_ttl"

    ok = await client.patch(
        URL, json={"access_token_ttl": 600, "refresh_token_ttl": 3600, "session_absolute_ttl": 7200}
    )
    assert ok.status_code == 200


async def test_access_ttl_is_capped(client: AsyncClient, as_owner: None):
    resp = await client.patch(URL, json={"access_token_ttl": 86_400})
    assert resp.status_code == 422
    assert resp.json()["errors"][0]["field"] == "access_token_ttl"


async def test_singleton_has_no_post_or_delete(client: AsyncClient, as_owner: None):
    assert (await client.post(URL, json={})).status_code == 405
    assert (await client.delete(URL)).status_code == 405


async def test_access_ttl_reaches_the_issued_token(client: AsyncClient, db_session: AsyncSession):
    await set_platform_settings(db_session, access_token_ttl=120)
    user = await create_user(db_session)

    resp = await client.post(
        "/api/v1/auth/login", json={"email": user.email, "password": DEFAULT_PASSWORD}
    )
    assert resp.status_code == 200
    claims = pyjwt.decode(resp.json()["access_token"], options={"verify_signature": False})
    assert claims["exp"] - claims["iat"] == 120


async def test_refresh_ttl_reaches_the_remember_me_cookie(
    client: AsyncClient, db_session: AsyncSession
):
    await set_platform_settings(db_session, refresh_token_ttl=3600)
    user = await create_user(db_session)

    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": DEFAULT_PASSWORD, "remember_me": True},
    )
    assert resp.status_code == 200
    assert "Max-Age=3600" in resp.headers["set-cookie"]


async def test_branding_is_public_and_minimal(client: AsyncClient):
    resp = await client.get(BRANDING_URL)
    assert resp.status_code == 200
    body = resp.json()
    assert body["org_name"] == "Achilles"
    assert set(body) == {
        "org_name",
        "org_logo_url",
        "accent_color",
        "timezone",
        "locale",
        "date_format",
    }, "no settings beyond the branding slice leak to anonymous callers"
