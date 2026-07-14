"""Bulk CSV invite — tests.html (P1)."""

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.auth.models import InviteToken
from tests.auth.integration.conftest import AuthorizeFn, Outbox
from tests.factories.users import create_user

pytestmark = [pytest.mark.integration, pytest.mark.p1]

BULK_URL = "/api/v1/invites/bulk"


async def _upload(client: AsyncClient, csv_text: str, **params: str):
    return await client.post(
        BULK_URL, params=params, files={"file": ("invites.csv", csv_text.encode())}
    )


async def test_partial_success_207(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn, outbox: Outbox
):
    admin = await create_user(db_session, role="admin")
    await create_user(db_session, email="existing@example.com")
    await authorize(admin.email)

    csv_text = "\n".join(
        [
            "good1@example.com",
            "existing@example.com",  # conflict: account exists
            "not-an-email",  # invalid
            "good2@example.com,member",
            "vip@example.com,admin",  # scope: admin cannot grant admin
        ]
    )
    resp = await _upload(client, csv_text)
    assert resp.status_code == 207
    rows = {r["email"]: r for r in resp.json()["results"]}
    assert rows["good1@example.com"]["status"] == "created"
    assert rows["existing@example.com"]["status"] == "conflict"
    assert rows["not-an-email"]["status"] == "invalid"
    assert rows["good2@example.com"]["status"] == "created"
    # An admin granting admin is a role-permission miss, not an "already exists"
    # conflict — it must classify apart, with a stable message token.
    assert rows["vip@example.com"]["status"] == "invalid"
    assert rows["vip@example.com"]["message"] == "role_forbidden"

    assert len(outbox.invites) == 2, "letters go only to created rows"
    created = await db_session.scalar(sa.select(sa.func.count()).select_from(InviteToken))
    assert created == 2, "valid rows are not rolled back by invalid ones"


async def test_in_batch_duplicates_reported(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn, outbox: Outbox
):
    admin = await create_user(db_session, role="admin")
    await authorize(admin.email)
    resp = await _upload(client, "dup@example.com\ndup@example.com\n")
    statuses = [r["status"] for r in resp.json()["results"]]
    assert statuses == ["created", "duplicate"]


async def test_reupload_is_idempotent(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn, outbox: Outbox
):
    admin = await create_user(db_session, role="admin")
    await authorize(admin.email)
    await _upload(client, "same@example.com\n")
    await _upload(client, "same@example.com\n")

    pending = await db_session.scalar(
        sa.select(sa.func.count()).select_from(InviteToken).where(InviteToken.accepted_at.is_(None))
    )
    assert pending == 1, "re-upload re-issues the link instead of stacking invites"


async def test_row_cap_is_422(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn, outbox: Outbox
):
    admin = await create_user(db_session, role="admin")
    await authorize(admin.email)
    huge = "\n".join(f"user{i}@example.com" for i in range(501))
    resp = await _upload(client, huge)
    assert resp.status_code == 422


async def test_dry_run_reports_without_side_effects(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn, outbox: Outbox
):
    admin = await create_user(db_session, role="admin")
    await create_user(db_session, email="existing@example.com")
    await authorize(admin.email)
    csv_text = "\n".join(
        [
            "good@example.com",
            "existing@example.com",  # conflict: account exists
            "not-an-email",  # invalid
            "dup@example.com",
            "dup@example.com",  # duplicate within the batch
        ]
    )

    preview = await _upload(client, csv_text, dry_run="true")
    assert preview.status_code == 207
    by_email = {r["email"]: r["status"] for r in preview.json()["results"]}
    assert by_email["good@example.com"] == "created"  # reads as "will be created"
    assert by_email["existing@example.com"] == "conflict"
    assert by_email["not-an-email"] == "invalid"
    assert [r["status"] for r in preview.json()["results"] if r["email"] == "dup@example.com"] == [
        "created",
        "duplicate",
    ]

    assert outbox.invites == [], "a dry run queues no letters"
    persisted = await db_session.scalar(sa.select(sa.func.count()).select_from(InviteToken))
    assert persisted == 0, "a dry run persists nothing"
    audited = await db_session.scalar(
        sa.text("SELECT count(*) FROM audit_log WHERE action = 'invite.create'")
    )
    assert audited == 0, "a dry run leaves no audit trace"

    # The real run classifies every row exactly as the preview did.
    real = await _upload(client, csv_text)
    assert [r["status"] for r in real.json()["results"]] == [
        r["status"] for r in preview.json()["results"]
    ]
    assert len(outbox.invites) == 2


async def test_default_role_fills_bare_rows_only(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn, outbox: Outbox
):
    owner = await create_user(db_session, role="owner")  # Owner may grant admin
    await authorize(owner.email)

    resp = await _upload(
        client, "bare@example.com\nexplicit@example.com,member\n", default_role="admin"
    )
    assert resp.status_code == 207
    assert all(r["status"] == "created" for r in resp.json()["results"])

    roles = dict((await db_session.execute(sa.select(InviteToken.email, InviteToken.role))).all())
    assert roles == {"bare@example.com": "admin", "explicit@example.com": "member"}

    # The report carries each row's role and flags the default-filled ones, so
    # the preview can show which rows the default-role selector governs.
    by_email = {r["email"]: r for r in resp.json()["results"]}
    assert by_email["bare@example.com"]["role"] == "admin"
    assert by_email["bare@example.com"]["role_from_default"] is True
    assert by_email["explicit@example.com"]["role"] == "member"
    assert by_email["explicit@example.com"]["role_from_default"] is False


async def test_binary_upload_is_422_not_500(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn, outbox: Outbox
):
    """A renamed Numbers/Excel doc (NUL bytes) is rejected cleanly, not as a 500."""
    admin = await create_user(db_session, role="admin")
    await authorize(admin.email)
    resp = await client.post(
        BULK_URL, files={"file": ("templat.csv", b"PK\x03\x04\x00\x00binary\x00garbage")}
    )
    assert resp.status_code == 422
    assert "CSV" in resp.json()["detail"]


async def test_smtp_unconfigured_is_409(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    await authorize(admin.email)
    assert (await _upload(client, "x@example.com\n")).status_code == 409
