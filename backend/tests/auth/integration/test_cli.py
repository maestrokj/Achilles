"""Operator CLI: init-owner · reset-password — tests.html (P1)."""

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.auth.models import RefreshToken, User
from achilles.cli import build_parser, init_owner, reset_password
from achilles.config import Settings
from tests.factories.users import DEFAULT_PASSWORD, create_user
from tests.factories.users import DEFAULT_PASSWORD as STRONG_PASSWORD

pytestmark = [pytest.mark.integration, pytest.mark.p1]


async def test_init_owner_creates_owner(test_settings: Settings, db_session: AsyncSession):
    code = await init_owner(
        test_settings, email="cli@example.com", password=STRONG_PASSWORD, full_name="CLI Owner"
    )
    assert code == 0
    user = await db_session.scalar(sa.select(User))
    assert user is not None
    assert user.role == "owner"
    assert user.email == "cli@example.com"


async def test_init_owner_refuses_second_run(test_settings: Settings, db_session: AsyncSession):
    await create_user(db_session)
    code = await init_owner(
        test_settings, email="cli@example.com", password=STRONG_PASSWORD, full_name="CLI Owner"
    )
    assert code == 1


async def test_init_owner_rejects_weak_password(test_settings: Settings, db_session: AsyncSession):
    code = await init_owner(
        test_settings, email="cli@example.com", password="password123", full_name="CLI Owner"
    )
    assert code == 1
    assert await db_session.scalar(sa.select(sa.func.count()).select_from(User)) == 0


async def test_init_owner_rejects_unloginable_email(
    test_settings: Settings, db_session: AsyncSession
):
    """The CLI has no schema in front of it: an owner the login schema refuses
    would brick the one-shot bootstrap."""
    code = await init_owner(
        test_settings, email="cli@dev.local", password=STRONG_PASSWORD, full_name="CLI Owner"
    )
    assert code == 1
    assert await db_session.scalar(sa.select(sa.func.count()).select_from(User)) == 0


async def test_reset_password_issues_temp_and_kills_sessions(
    test_settings: Settings,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
):
    user = await create_user(db_session)
    db_session.add(
        RefreshToken(
            user_id=user.id,
            token_hash="x" * 64,
            family_id=uuid.uuid7(),
            expires_at=sa.func.now(),
            absolute_expires_at=sa.func.now(),
        )
    )
    await db_session.commit()

    assert await reset_password(test_settings, email=user.email) == 0
    printed = capsys.readouterr().out
    assert "Temporary password" in printed

    fresh = await db_session.get(User, user.id, populate_existing=True)
    assert fresh is not None
    assert fresh.must_change_password is True
    assert fresh.password_hash != user.password_hash or DEFAULT_PASSWORD not in printed
    sessions = await db_session.scalar(sa.select(sa.func.count()).select_from(RefreshToken))
    assert sessions == 0


async def test_reset_password_unknown_email(test_settings: Settings):
    assert await reset_password(test_settings, email="ghost@example.com") == 1


def test_parser_wiring():
    args = build_parser().parse_args(
        ["init-owner", "--email", "a@b.c", "--password", "x", "--name", "A"]
    )
    assert args.command == "init-owner"
    assert args.email == "a@b.c"

    args = build_parser().parse_args(["reset-password", "--email", "a@b.c"])
    assert args.command == "reset-password"
