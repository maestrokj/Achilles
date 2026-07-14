"""Messenger link: code issue + confirm — tests.html (P1)."""

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
import time_machine
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.auth.models import IdentityMapping
from tests.auth.integration.conftest import AuthorizeFn
from tests.factories.users import create_user

pytestmark = [pytest.mark.integration, pytest.mark.p1]


def _confirm_payload(code: str, chat_id: str = "U123") -> dict[str, str]:
    return {"code": code, "platform_user_id": chat_id}


async def _issue(client: AsyncClient, platform: str = "slack") -> str:
    resp = await client.post(f"/api/v1/link/{platform}")
    assert resp.status_code == 201, resp.text
    return resp.json()["code"]


async def test_full_link_flow(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    user = await create_user(db_session)
    await authorize(user.email)
    code = await _issue(client)

    client.headers.pop("Authorization")  # the bot side is not the web session
    resp = await client.post("/api/v1/link/slack/confirm", json=_confirm_payload(code))
    assert resp.status_code == 200
    assert resp.json() == {"user_id": user.id, "status": "linked"}

    mapping = await db_session.scalar(sa.select(IdentityMapping))
    assert mapping is not None
    assert (mapping.source, mapping.source_user_id) == ("slack", "U123")


async def test_issued_code_is_short_and_human_typeable(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    user = await create_user(db_session)
    await authorize(user.email)
    code = await _issue(client)
    # "K7P2-9XQ4": short, dash-grouped, uppercase — not a 43-char URL token.
    assert "-" in code
    assert len(code.replace("-", "")) == 8


async def test_confirm_forgives_case_and_missing_dash(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    user = await create_user(db_session)
    await authorize(user.email)
    code = await _issue(client)
    client.headers.pop("Authorization")
    # The user types it lower-case without the dash — it still links.
    typed = code.lower().replace("-", "")
    resp = await client.post("/api/v1/link/slack/confirm", json=_confirm_payload(typed))
    assert resp.status_code == 200
    assert resp.json()["status"] == "linked"


async def test_unknown_platform_is_422(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    user = await create_user(db_session)
    await authorize(user.email)
    assert (await client.post("/api/v1/link/icq")).status_code == 422


async def test_telegram_is_code_only_platform(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    user = await create_user(db_session)
    await authorize(user.email)
    code = await _issue(client, platform="telegram")
    client.headers.pop("Authorization")
    resp = await client.post("/api/v1/link/telegram/confirm", json=_confirm_payload(code))
    assert resp.status_code == 200


async def test_expired_code_is_410(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    user = await create_user(db_session)
    with time_machine.travel(datetime(2026, 7, 2, 12, 0, tzinfo=UTC), tick=False) as traveller:
        await authorize(user.email)
        code = await _issue(client)
        traveller.shift(timedelta(minutes=16))
        client.headers.pop("Authorization")
        resp = await client.post("/api/v1/link/slack/confirm", json=_confirm_payload(code))
    assert resp.status_code == 410
    assert resp.json()["code"] == "LINK_EXPIRED"


async def test_code_reuse_is_410(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    user = await create_user(db_session)
    await authorize(user.email)
    code = await _issue(client)
    client.headers.pop("Authorization")
    assert (
        await client.post("/api/v1/link/slack/confirm", json=_confirm_payload(code))
    ).status_code == 200
    reuse = await client.post(
        "/api/v1/link/slack/confirm", json=_confirm_payload(code, chat_id="U999")
    )
    assert reuse.status_code == 410


async def test_already_linked_chat_is_409(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    alice = await create_user(db_session)
    bob = await create_user(db_session)
    await authorize(alice.email)
    code = await _issue(client)
    await client.post("/api/v1/link/slack/confirm", json=_confirm_payload(code))

    await authorize(bob.email)
    second_code = await _issue(client)
    client.headers.pop("Authorization")
    resp = await client.post("/api/v1/link/slack/confirm", json=_confirm_payload(second_code))
    assert resp.status_code == 409
    assert resp.json()["code"] == "ALREADY_LINKED"


async def test_new_code_invalidates_previous(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    user = await create_user(db_session)
    await authorize(user.email)
    first = await _issue(client)
    second = await _issue(client)
    client.headers.pop("Authorization")
    assert (
        await client.post("/api/v1/link/slack/confirm", json=_confirm_payload(first))
    ).status_code == 410
    assert (
        await client.post("/api/v1/link/slack/confirm", json=_confirm_payload(second))
    ).status_code == 200


async def test_five_wrong_codes_drop_the_barrier(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    user = await create_user(db_session)
    await authorize(user.email)
    real_code = await _issue(client)
    client.headers.pop("Authorization")

    for _ in range(5):
        wrong = await client.post("/api/v1/link/slack/confirm", json=_confirm_payload("wrong-code"))
        assert wrong.status_code == 410
    refused = await client.post("/api/v1/link/slack/confirm", json=_confirm_payload(real_code))
    assert refused.status_code == 429, "the 6th attempt from this chat is refused, even correct"
