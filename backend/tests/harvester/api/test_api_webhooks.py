"""Inbound webhook: signature gate, dedup, incremental trigger, rotation grace (API)."""

import hashlib
import hmac

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.auth.constants import UserRole
from achilles.harvester.constants import WEBHOOK_REJECT_ALERT_THRESHOLD, SyncTrigger
from achilles.harvester.models import SyncRun
from achilles.notifications.models import Notification
from tests.auth.integration.conftest import AuthorizeFn
from tests.factories.knowledge import create_source
from tests.factories.users import create_user

pytestmark = [pytest.mark.api, pytest.mark.p1]

ADMIN = "/api/v1/sources"
HOOK = "/api/v1/harvester/webhooks/sources"
BODY = b'{"issue":"ENG-1"}'


@pytest.fixture
async def as_admin(db_session: AsyncSession, authorize: AuthorizeFn) -> None:
    admin = await create_user(db_session, role=UserRole.ADMIN.value)
    await authorize(admin.email)


def sign(secret: str, body: bytes = BODY) -> dict[str, str]:
    """A properly signed Jira delivery: HMAC-SHA256 in X-Hub-Signature."""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {"X-Hub-Signature": f"sha256={digest}", "Content-Type": "application/json"}


async def _enable_with_secret(client: AsyncClient, source_id: int) -> str:
    """Rotate a secret in, then switch the channel on — the admin path."""
    secret = (await client.post(f"{ADMIN}/{source_id}/webhook/rotate")).json()["secret"]
    enabled = await client.patch(f"{ADMIN}/{source_id}", json={"webhook_enabled": True})
    assert enabled.status_code == 200
    return secret


async def _webhook_run_count(session: AsyncSession, source_id: int) -> int:
    return (
        await session.scalar(
            sa.select(sa.func.count())
            .select_from(SyncRun)
            .where(SyncRun.source_id == source_id, SyncRun.trigger == str(SyncTrigger.WEBHOOK))
        )
    ) or 0


@pytest.mark.usefixtures("as_admin")
async def test_signed_delivery_triggers_incremental_pull(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    source = await create_source(db_session, connector_type="jira")
    secret = await _enable_with_secret(client, source.id)

    resp = await client.post(f"{HOOK}/{source.id}", content=BODY, headers=sign(secret))
    assert resp.status_code == 200
    assert await _webhook_run_count(db_session, source.id) == 1


@pytest.mark.usefixtures("as_admin")
async def test_bad_signature_is_401_and_starts_nothing(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    source = await create_source(db_session, connector_type="jira")
    await _enable_with_secret(client, source.id)

    resp = await client.post(f"{HOOK}/{source.id}", content=BODY, headers=sign("the-wrong-secret"))
    assert resp.status_code == 401
    assert await _webhook_run_count(db_session, source.id) == 0


async def _rejection_alert_count(session: AsyncSession) -> int:
    return (
        await session.scalar(
            sa.select(sa.func.count())
            .select_from(Notification)
            .where(Notification.title == "security.webhook_rejected")
        )
    ) or 0


@pytest.mark.usefixtures("as_admin")
async def test_rejection_spike_raises_one_security_alert(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    source = await create_source(db_session, connector_type="jira")
    await _enable_with_secret(client, source.id)
    bad = sign("the-wrong-secret")

    async def reject_once() -> None:
        resp = await client.post(f"{HOOK}/{source.id}", content=BODY, headers=bad)
        assert resp.status_code == 401

    # One below the threshold: rejected, but too few to alarm.
    for _ in range(WEBHOOK_REJECT_ALERT_THRESHOLD - 1):
        await reject_once()
    assert await _rejection_alert_count(db_session) == 0

    # The threshold call fires the alert; further ones stay quiet (dedup window).
    for _ in range(3):
        await reject_once()
    assert await _rejection_alert_count(db_session) == 1


@pytest.mark.usefixtures("as_admin")
async def test_disabled_channel_is_a_silent_ack(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # A secret exists but the toggle is off — the hook stays silent (no 401 leak).
    source = await create_source(db_session, connector_type="jira")
    secret = (await client.post(f"{ADMIN}/{source.id}/webhook/rotate")).json()["secret"]

    resp = await client.post(f"{HOOK}/{source.id}", content=BODY, headers=sign(secret))
    assert resp.status_code == 200
    assert await _webhook_run_count(db_session, source.id) == 0


async def test_unknown_source_is_a_silent_ack(client: AsyncClient) -> None:
    resp = await client.post(f"{HOOK}/999999", content=BODY, headers=sign("x"))
    assert resp.status_code == 200


@pytest.mark.usefixtures("as_admin")
async def test_replay_of_one_delivery_starts_a_single_run(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    source = await create_source(db_session, connector_type="jira")
    secret = await _enable_with_secret(client, source.id)
    headers = {**sign(secret), "X-Atlassian-Webhook-Identifier": "delivery-1"}

    first = await client.post(f"{HOOK}/{source.id}", content=BODY, headers=headers)
    second = await client.post(f"{HOOK}/{source.id}", content=BODY, headers=headers)
    assert first.status_code == second.status_code == 200
    assert await _webhook_run_count(db_session, source.id) == 1


@pytest.mark.usefixtures("as_admin")
async def test_rotated_secret_keeps_verifying_within_the_grace_window(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    source = await create_source(db_session, connector_type="jira")
    old_secret = await _enable_with_secret(client, source.id)
    # Rotate again: the previous secret must still authenticate a delivery in flight.
    await client.post(f"{ADMIN}/{source.id}/webhook/rotate")

    resp = await client.post(
        f"{HOOK}/{source.id}",
        content=BODY,
        headers={**sign(old_secret), "X-Atlassian-Webhook-Identifier": "grace-1"},
    )
    assert resp.status_code == 200
    assert await _webhook_run_count(db_session, source.id) == 1


@pytest.mark.usefixtures("as_admin")
async def test_enable_without_secret_is_rejected(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    source = await create_source(db_session, connector_type="jira")
    resp = await client.patch(f"{ADMIN}/{source.id}", json={"webhook_enabled": True})
    assert resp.status_code == 422


@pytest.mark.usefixtures("as_admin")
async def test_rotate_unsupported_connector_is_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    source = await create_source(db_session, connector_type="confluence")  # webhooks=False
    resp = await client.post(f"{ADMIN}/{source.id}/webhook/rotate")
    assert resp.status_code == 422


@pytest.mark.usefixtures("as_admin")
async def test_source_out_exposes_webhook_fields(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    source = await create_source(db_session, connector_type="jira")
    (row,) = (await client.get(ADMIN)).json()
    assert row["webhook_supported"] is True
    assert row["webhook_enabled"] is False
    assert row["webhook_secret_set"] is False
    assert row["webhook_endpoint_url"].endswith(f"/harvester/webhooks/sources/{source.id}")
