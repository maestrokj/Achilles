"""Backup settings + snapshot list API: write-only creds, Owner gate, cadence rule."""

import json

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.auth.integration.conftest import AuthorizeFn
from tests.factories.users import create_user

from achilles.knowledge_store.constants import BackupState
from achilles.knowledge_store.services import backups

pytestmark = [pytest.mark.integration, pytest.mark.p1]

URL = "/api/v1/admin/knowledge/backup-settings"
LIST_URL = "/api/v1/admin/knowledge/backups"

CREDS = json.dumps({"access_key": "AK", "secret_key": "SK", "endpoint_url": "http://minio:9000"})


async def test_patch_encrypts_credential_and_never_returns_it(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    owner = await create_user(db_session, role="owner")
    await authorize(owner.email)

    body = (
        await client.patch(
            URL, json={"destination_url": "s3://acme-backups/prod", "credential": CREDS}
        )
    ).json()
    assert body["destination_url"] == "s3://acme-backups/prod"
    assert body["credential_is_set"] is True
    assert "credential" not in body and "AK" not in json.dumps(body)

    row = await backups.get_settings(db_session)
    assert row.destination_creds_enc is not None
    assert "SK" not in row.destination_creds_enc  # at rest — ciphertext, not JSON

    # "" clears; absent keeps.
    kept = (await client.patch(URL, json={"retention_count": 7})).json()
    assert kept["credential_is_set"] is True and kept["retention_count"] == 7
    cleared = (await client.patch(URL, json={"credential": ""})).json()
    assert cleared["credential_is_set"] is False


async def test_patch_rejects_malformed_credential_json(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    owner = await create_user(db_session, role="owner")
    await authorize(owner.email)
    response = await client.patch(URL, json={"credential": "not-json"})
    assert response.status_code == 422


async def test_weekly_requires_weekday_daily_clears_it(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    owner = await create_user(db_session, role="owner")
    await authorize(owner.email)

    assert (await client.patch(URL, json={"frequency": "weekly"})).status_code == 422
    weekly = (await client.patch(URL, json={"frequency": "weekly", "weekday": 6})).json()
    assert (weekly["frequency"], weekly["weekday"]) == ("weekly", 6)

    daily = (await client.patch(URL, json={"frequency": "daily"})).json()
    assert (daily["frequency"], daily["weekday"]) == ("daily", None)

    assert (await client.patch(URL, json={"time": "25:00"})).status_code == 422


async def test_admin_reads_but_cannot_write(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    await authorize(admin.email)

    read = (await client.get(URL)).json()
    assert read["credential_is_set"] is False

    assert (await client.patch(URL, json={"retention_count": 3})).status_code == 403


async def test_snapshot_list_is_newest_first(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    first = await backups.start_snapshot(db_session)
    await backups.finish_snapshot(
        db_session, first, state=str(BackupState.SUCCEEDED), size_bytes=10, location="file:///a"
    )
    await db_session.commit()
    second = await backups.start_snapshot(db_session)
    await backups.finish_snapshot(db_session, second, state=str(BackupState.FAILED), error="boom")
    await db_session.commit()
    # Journal order follows started_at — nudge the first one back explicitly.
    await db_session.execute(
        sa.text(
            "UPDATE backup_snapshots SET started_at = started_at - interval '1 hour' WHERE id = :id"
        ),
        {"id": first},
    )
    await db_session.commit()
    await authorize(admin.email)

    body = (await client.get(LIST_URL)).json()
    assert [row["id"] for row in body] == [second, first]
    assert body[0]["error"] == "boom"
    assert body[1]["size_bytes"] == 10
    assert "location" not in body[1]  # internal pointer, not an API field
