"""The sidebar contract: list / rename / delete of one's own web conversations.

Rows are seeded straight into the tables — no SSE turns, no LLM mocks; the
contract under test is ownership, the surface filter, activity ordering and
the cascade, not the turn pipeline (test_api_chat covers that).
"""

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.query_engine.constants import TITLE_MAX_CHARS, MessageRole, Surface
from achilles.query_engine.models import Conversation, Message
from tests.auth.integration.conftest import AuthorizeFn
from tests.factories.users import User, create_user

pytestmark = [pytest.mark.api, pytest.mark.p1]


@pytest.fixture
async def member(db_session: AsyncSession, authorize: AuthorizeFn) -> User:
    user = await create_user(db_session)
    await authorize(user.email)
    return user


async def make_conversation(
    db_session: AsyncSession,
    *,
    user_id: int,
    surface: Surface = Surface.WEB,
    title: str = "chat",
    messages: int = 1,
) -> int:
    conversation = Conversation(user_id=user_id, surface=str(surface), title=title)
    db_session.add(conversation)
    await db_session.flush()
    conversation_id = conversation.id
    for n in range(messages):
        db_session.add(
            Message(conversation_id=conversation_id, role=MessageRole.USER.value, content=f"m{n}")
        )
    await db_session.commit()
    return conversation_id


async def add_message(db_session: AsyncSession, conversation_id: int) -> None:
    db_session.add(
        Message(conversation_id=conversation_id, role=MessageRole.USER.value, content="more")
    )
    await db_session.commit()


# --- List ---


async def test_list_orders_by_last_activity(
    client: AsyncClient, db_session: AsyncSession, member: User
):
    first = await make_conversation(db_session, user_id=member.id, title="first")
    second = await make_conversation(db_session, user_id=member.id, title="second")
    await add_message(db_session, first)  # the older dialogue wakes up

    resp = await client.get("/api/v1/conversations")

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"items", "total", "page", "per_page"}
    assert body["total"] == 2
    assert [item["id"] for item in body["items"]] == [first, second]


async def test_list_ordering_rides_timestamps_not_ids(
    client: AsyncClient, db_session: AsyncSession, member: User
):
    # Backdated rows (seeds, imports): insertion order says one thing, the
    # message timestamps the opposite — the timestamps must win.
    stale = await make_conversation(db_session, user_id=member.id, title="stale", messages=0)
    fresh = await make_conversation(db_session, user_id=member.id, title="fresh", messages=0)
    now = datetime.now(UTC)
    db_session.add(
        Message(
            conversation_id=fresh,
            role=MessageRole.USER.value,
            content="new",
            created_at=now,
        )
    )
    db_session.add(
        Message(
            conversation_id=stale,
            role=MessageRole.USER.value,
            content="old",
            created_at=now - timedelta(days=3),
        )
    )
    await db_session.commit()

    body = (await client.get("/api/v1/conversations")).json()

    assert [item["id"] for item in body["items"]] == [fresh, stale]


async def test_list_keeps_only_own_web_conversations(
    client: AsyncClient, db_session: AsyncSession, member: User
):
    mine = await make_conversation(db_session, user_id=member.id)
    await make_conversation(db_session, user_id=member.id, surface=Surface.SLACK)
    stranger = await create_user(db_session)
    await make_conversation(db_session, user_id=stranger.id)

    body = (await client.get("/api/v1/conversations")).json()

    assert [item["id"] for item in body["items"]] == [mine]
    assert body["total"] == 1


async def test_list_last_activity_is_the_newest_message(
    client: AsyncClient, db_session: AsyncSession, member: User
):
    empty = await make_conversation(db_session, user_id=member.id, messages=0)
    busy = await make_conversation(db_session, user_id=member.id)
    await add_message(db_session, busy)
    newest = await db_session.scalar(
        sa.select(sa.func.max(Message.created_at)).where(Message.conversation_id == busy)
    )
    assert newest is not None

    items = {
        item["id"]: item for item in (await client.get("/api/v1/conversations")).json()["items"]
    }

    assert datetime.fromisoformat(items[busy]["last_activity_at"]) == newest
    # No messages → the conversation's own birth stands in for activity.
    assert items[empty]["last_activity_at"] == items[empty]["created_at"]


async def test_list_anonymous_is_401(client: AsyncClient, db_session: AsyncSession):
    assert (await client.get("/api/v1/conversations")).status_code == 401


# --- Rename ---


async def test_rename_trims_and_persists(
    client: AsyncClient, db_session: AsyncSession, member: User
):
    conversation_id = await make_conversation(db_session, user_id=member.id, title="old")

    resp = await client.patch(
        f"/api/v1/conversations/{conversation_id}", json={"title": "  Deploy notes  "}
    )

    assert resp.status_code == 204
    title = await db_session.scalar(
        sa.select(Conversation.title).where(Conversation.id == conversation_id)
    )
    assert title == "Deploy notes"


async def test_rename_rejects_blank_and_overlong_titles(
    client: AsyncClient, db_session: AsyncSession, member: User
):
    conversation_id = await make_conversation(db_session, user_id=member.id)
    url = f"/api/v1/conversations/{conversation_id}"

    assert (await client.patch(url, json={"title": "   "})).status_code == 422
    assert (await client.patch(url, json={"title": "x" * (TITLE_MAX_CHARS + 1)})).status_code == 422


async def test_rename_foreign_is_404(
    client: AsyncClient, db_session: AsyncSession, member: User, authorize: AuthorizeFn
):
    conversation_id = await make_conversation(db_session, user_id=member.id, title="mine")
    stranger = await create_user(db_session)
    await authorize(stranger.email)

    resp = await client.patch(f"/api/v1/conversations/{conversation_id}", json={"title": "stolen"})

    assert resp.status_code == 404
    title = await db_session.scalar(
        sa.select(Conversation.title).where(Conversation.id == conversation_id)
    )
    assert title == "mine"


# --- Delete ---


async def test_delete_cascades_messages(
    client: AsyncClient, db_session: AsyncSession, member: User
):
    conversation_id = await make_conversation(db_session, user_id=member.id, messages=2)

    resp = await client.delete(f"/api/v1/conversations/{conversation_id}")

    assert resp.status_code == 204
    assert await db_session.get(Conversation, conversation_id) is None
    remaining = await db_session.scalar(
        sa.select(sa.func.count())
        .select_from(Message)
        .where(Message.conversation_id == conversation_id)
    )
    assert remaining == 0
    # Gone means gone: the second attempt finds nothing to disclose.
    assert (await client.delete(f"/api/v1/conversations/{conversation_id}")).status_code == 404


async def test_delete_foreign_is_404(
    client: AsyncClient, db_session: AsyncSession, member: User, authorize: AuthorizeFn
):
    conversation_id = await make_conversation(db_session, user_id=member.id)
    stranger = await create_user(db_session)
    await authorize(stranger.email)

    assert (await client.delete(f"/api/v1/conversations/{conversation_id}")).status_code == 404
    assert await db_session.get(Conversation, conversation_id) is not None
