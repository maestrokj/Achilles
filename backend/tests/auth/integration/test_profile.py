"""Self-service profile: read /me with catalogues, edit name and region.

Design: auth-security/_wireframes/profile-account.html (Language and region).
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.auth.integration.conftest import AuthorizeFn
from tests.factories.users import create_user

pytestmark = [pytest.mark.integration, pytest.mark.p1]

ME_URL = "/api/v1/auth/me"


async def test_me_returns_user_and_catalogues(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    user = await create_user(db_session)
    await authorize(user.email)

    resp = await client.get(ME_URL)
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["email"] == user.email
    assert set(body["locale_choices"]) == {"ru", "en"}
    assert "DD.MM.YYYY" in body["date_format_choices"]


async def test_patch_updates_name_and_region(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    user = await create_user(db_session)
    await authorize(user.email)

    resp = await client.patch(
        ME_URL,
        json={
            "full_name": "Renamed Self",
            "timezone": "Europe/Berlin",
            "locale": "en",
            "date_format": "YYYY-MM-DD",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["full_name"] == "Renamed Self"

    await db_session.refresh(user)
    assert user.full_name == "Renamed Self"
    assert user.timezone == "Europe/Berlin"
    assert user.locale == "en"
    assert user.date_format == "YYYY-MM-DD"


async def test_patch_is_partial(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    user = await create_user(db_session, full_name="Keep Me")
    await authorize(user.email)

    resp = await client.patch(ME_URL, json={"locale": "ru"})
    assert resp.status_code == 200

    await db_session.refresh(user)
    assert user.locale == "ru"
    assert user.full_name == "Keep Me"  # untouched


async def test_patch_none_clears_to_org_default(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    user = await create_user(db_session)
    await authorize(user.email)
    await client.patch(ME_URL, json={"timezone": "Asia/Tokyo"})

    resp = await client.patch(ME_URL, json={"timezone": None})
    assert resp.status_code == 200

    await db_session.refresh(user)
    assert user.timezone is None


async def test_patch_rejects_unknown_timezone(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    user = await create_user(db_session)
    await authorize(user.email)
    resp = await client.patch(ME_URL, json={"timezone": "Mars/Olympus"})
    assert resp.status_code == 422


async def test_patch_rejects_unknown_locale(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    user = await create_user(db_session)
    await authorize(user.email)
    resp = await client.patch(ME_URL, json={"locale": "de"})
    assert resp.status_code == 422


async def test_patch_rejects_empty_name(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    user = await create_user(db_session)
    await authorize(user.email)
    resp = await client.patch(ME_URL, json={"full_name": ""})
    assert resp.status_code == 422


async def test_patch_rejects_null_name(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    # full_name is NOT NULL; an explicit null must 422 at the schema, not 500 on commit.
    user = await create_user(db_session)
    await authorize(user.email)
    resp = await client.patch(ME_URL, json={"full_name": None})
    assert resp.status_code == 422


async def test_patch_cannot_change_email_or_role(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    """Unknown keys are ignored by the schema — email and role stay admin territory."""
    user = await create_user(db_session)
    original_email = user.email
    await authorize(user.email)
    resp = await client.patch(ME_URL, json={"email": "new@example.com", "role": "owner"})
    assert resp.status_code == 200

    await db_session.refresh(user)
    assert user.email == original_email
    assert user.role == "member"


async def test_me_requires_auth(client: AsyncClient):
    assert (await client.get(ME_URL)).status_code == 401
