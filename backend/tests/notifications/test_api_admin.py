"""Admin config API: RBAC, builtin guards, the lock, webhook lifecycle (API)."""

import pytest
import respx
import sqlalchemy as sa
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.notifications.models import NotificationChannel, NotificationRoute
from tests.auth.integration.conftest import AuthorizeFn
from tests.factories.users import create_user
from tests.notifications.conftest import WEBHOOK_URL

pytestmark = [pytest.mark.api, pytest.mark.p1]

CHANNELS = "/api/v1/admin/notification-channels"
ROUTES = "/api/v1/admin/notification-routes"

WEBHOOK_BODY = {
    "name": "Ops",
    "preset": "generic",
    "url": WEBHOOK_URL,
    "secret": "hook-secret",
}


async def _login_owner(db_session: AsyncSession, authorize: AuthorizeFn) -> None:
    owner = await create_user(db_session, role="owner")
    await authorize(owner.email)


async def test_member_cannot_read_admin_can(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    member = await create_user(db_session)
    await authorize(member.email)
    assert (await client.get(CHANNELS)).status_code == 403

    admin = await create_user(db_session, role="admin")
    await authorize(admin.email)
    body = (await client.get(CHANNELS)).json()
    assert [(c["kind"], c["is_builtin"]) for c in body["items"]] == [
        ("in_app", True),
        ("email", True),
    ]
    assert (await client.post(CHANNELS, json=WEBHOOK_BODY)).status_code == 403, "write is Owner's"


async def test_webhook_lifecycle_with_masked_secrets(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    await _login_owner(db_session, authorize)

    created = await client.post(CHANNELS, json=WEBHOOK_BODY)
    assert created.status_code == 201
    body = created.json()
    assert body["url_mask"] and WEBHOOK_URL not in body["url_mask"]
    assert body["secret_set"] is True

    row = await db_session.scalar(
        sa.select(NotificationChannel).where(NotificationChannel.id == body["id"])
    )
    assert row is not None and row.url_enc is not None and row.url_enc.startswith("v1:")

    # broadcast route cells are seeded disabled; targeted types get none
    cells = (await client.get(ROUTES)).json()["items"]
    webhook_cells = [c for c in cells if c["channel_id"] == body["id"]]
    assert {c["event_type"] for c in webhook_cells} == {
        "sync",
        "security",
        "budget",
        "system",
        "discovery",
    }
    assert all(c["enabled"] is False for c in webhook_cells)

    assert (await client.delete(f"{CHANNELS}/{body['id']}")).status_code == 204
    remaining = (await client.get(ROUTES)).json()["items"]
    assert all(c["channel_id"] != body["id"] for c in remaining)


async def test_builtin_channels_are_protected(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    await _login_owner(db_session, authorize)
    channels = (await client.get(CHANNELS)).json()["items"]
    in_app = next(c for c in channels if c["kind"] == "in_app")
    email = next(c for c in channels if c["kind"] == "email")

    assert (await client.delete(f"{CHANNELS}/{in_app['id']}")).status_code == 409
    assert (
        await client.patch(f"{CHANNELS}/{in_app['id']}", json={"enabled": False})
    ).status_code == 409, "the in-app rail cannot be switched off"
    assert (
        await client.patch(f"{CHANNELS}/{in_app['id']}", json={"name": "X"})
    ).status_code == 409, "builtins have no editable fields"

    # pausing the email channel org-wide is a legitimate lever
    resp = await client.patch(f"{CHANNELS}/{email['id']}", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


async def test_route_cells_carry_category_severity(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    """Every cell answers the row badge: the loudest catalog event of its category."""
    await _login_owner(db_session, authorize)
    cells = (await client.get(ROUTES)).json()["items"]
    assert cells, "the matrix is seeded"
    severity_by_type = {c["event_type"]: c["severity"] for c in cells}
    assert severity_by_type["sync"] == "critical"
    assert severity_by_type["agent"] == "warning"
    assert set(severity_by_type.values()) <= {"info", "warning", "critical"}


async def test_locked_cell_refuses_to_close(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    await _login_owner(db_session, authorize)
    cells = (await client.get(ROUTES)).json()["items"]
    in_app_security = next(c for c in cells if c["event_type"] == "security" and c["locked"])

    refused = await client.patch(
        ROUTES,
        json={
            "items": [
                {
                    "event_type": "security",
                    "channel_id": in_app_security["channel_id"],
                    "enabled": False,
                }
            ]
        },
    )
    assert refused.status_code == 409

    # a free cell toggles fine
    email_cell = next(c for c in cells if c["event_type"] == "sync" and not c["locked"])
    ok = await client.patch(
        ROUTES,
        json={
            "items": [
                {"event_type": "sync", "channel_id": email_cell["channel_id"], "enabled": False}
            ]
        },
    )
    assert ok.status_code == 200
    updated = next(
        c
        for c in ok.json()["items"]
        if c["event_type"] == "sync" and c["channel_id"] == email_cell["channel_id"]
    )
    assert updated["enabled"] is False

    unknown = await client.patch(
        ROUTES, json={"items": [{"event_type": "sync", "channel_id": 9999, "enabled": True}]}
    )
    assert unknown.status_code == 404


async def test_missing_cell_is_created_on_toggle(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    """A gap in the grid (channel present, cell absent) materializes on toggle."""
    await _login_owner(db_session, authorize)
    channel_id = (await client.post(CHANNELS, json=WEBHOOK_BODY)).json()["id"]

    # Drop one seeded cell so the (discovery x webhook) pair is a genuine gap.
    await db_session.execute(
        sa.delete(NotificationRoute).where(
            NotificationRoute.channel_id == channel_id,
            NotificationRoute.event_type == "discovery",
        )
    )
    await db_session.commit()

    ok = await client.patch(
        ROUTES,
        json={"items": [{"event_type": "discovery", "channel_id": channel_id, "enabled": True}]},
    )
    assert ok.status_code == 200
    recreated = next(
        c
        for c in ok.json()["items"]
        if c["event_type"] == "discovery" and c["channel_id"] == channel_id
    )
    assert recreated["enabled"] is True and recreated["locked"] is False


async def test_channel_test_posts_and_stamps(
    client: AsyncClient,
    db_session: AsyncSession,
    authorize: AuthorizeFn,
    hibp_clean: respx.MockRouter,
):
    await _login_owner(db_session, authorize)
    created = (await client.post(CHANNELS, json=WEBHOOK_BODY)).json()
    hibp_clean.post(WEBHOOK_URL).mock(return_value=Response(200))

    resp = await client.post(f"{CHANNELS}/{created['id']}/test")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "error": None}
    shown = (await client.get(CHANNELS)).json()["items"]
    tested = next(c for c in shown if c["id"] == created["id"])
    assert tested["last_test_ok"] is True and tested["last_test_at"] is not None

    channels = (await client.get(CHANNELS)).json()["items"]
    email = next(c for c in channels if c["kind"] == "email")
    assert (await client.post(f"{CHANNELS}/{email['id']}/test")).status_code == 409


async def test_disabled_route_row_still_exists(db_session: AsyncSession):
    """Sanity: the seed carries a full matrix — 7 types x 2 builtin channels."""
    count = await db_session.scalar(sa.select(sa.func.count()).select_from(NotificationRoute))
    assert count == 14
