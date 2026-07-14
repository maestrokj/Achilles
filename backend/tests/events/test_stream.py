"""The unified push stream: hello scoping, board nudges, coalescing, bell, ping.

The generator is exercised directly: httpx's ASGITransport buffers a response
to completion, so an endless SSE endpoint cannot be consumed through it —
only the 401 (which completes) goes over HTTP.
"""

import asyncio
import json
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import Request
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from achilles.auth.constants import UserRole
from achilles.config import Settings
from achilles.events import routes as stream_module
from achilles.events.constants import Board
from achilles.events.publisher import publish_board
from achilles.events.routes import _event_stream
from achilles.notifications import dispatcher
from tests.factories.users import create_user

pytestmark = [pytest.mark.integration, pytest.mark.p1]

FRAME_TIMEOUT = 5.0


@pytest.fixture
async def redis_cache(test_settings: Settings) -> AsyncGenerator[Redis]:
    client = Redis.from_url(test_settings.redis_cache_url, decode_responses=True)
    yield client
    await client.aclose()


def fake_request(db_engine: AsyncEngine, redis_cache: Redis) -> Request:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    state = SimpleNamespace(
        redis=SimpleNamespace(cache=redis_cache),
        db=SimpleNamespace(pg_session_factory=factory),
    )
    return cast("Request", SimpleNamespace(state=state))


def _parse(frame: str) -> tuple[str, dict[str, object]]:
    lines = frame.split("\n")
    return lines[0].removeprefix("event: "), json.loads(lines[1].removeprefix("data: "))


async def _next_frame(stream: AsyncGenerator[str]) -> tuple[str, dict[str, object]]:
    return _parse(await asyncio.wait_for(anext(stream), FRAME_TIMEOUT))


async def _next_named(stream: AsyncGenerator[str], name: str) -> dict[str, object]:
    async def read() -> dict[str, object]:
        async for frame in stream:
            event, payload = _parse(frame)
            if event == name:
                return payload
        raise AssertionError(f"stream ended before a {name} frame")

    return await asyncio.wait_for(read(), FRAME_TIMEOUT)


async def test_hello_lists_boards_by_role(
    db_session: AsyncSession, db_engine: AsyncEngine, redis_cache: Redis
):
    member = await create_user(db_session)
    admin = await create_user(db_session, role=str(UserRole.ADMIN))
    request = fake_request(db_engine, redis_cache)

    member_stream = _event_stream(request, member.id, str(UserRole.MEMBER))
    try:
        event, payload = await _next_frame(member_stream)
        assert event == "hello"
        assert payload["boards"] == ["agents"], "a member sees only own agents"
    finally:
        await member_stream.aclose()

    admin_stream = _event_stream(request, admin.id, str(UserRole.ADMIN))
    try:
        event, payload = await _next_frame(admin_stream)
        assert event == "hello"
        assert payload["boards"] == ["agents", "harvester", "knowledge"]
    finally:
        await admin_stream.aclose()


async def test_board_nudge_reaches_the_admin(
    db_session: AsyncSession, db_engine: AsyncEngine, redis_cache: Redis
):
    admin = await create_user(db_session, role=str(UserRole.ADMIN))
    stream = _event_stream(fake_request(db_engine, redis_cache), admin.id, str(UserRole.ADMIN))
    try:
        await _next_named(stream, "unread")  # hello + opening counter pass
        await publish_board(redis_cache, Board.HARVESTER)
        payload = await _next_named(stream, "board")
        assert payload == {"board": "harvester"}
    finally:
        await stream.aclose()


async def test_member_does_not_receive_admin_boards(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    redis_cache: Redis,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(stream_module, "HEARTBEAT_SECONDS", 0.05)
    member = await create_user(db_session)
    stream = _event_stream(fake_request(db_engine, redis_cache), member.id, str(UserRole.MEMBER))
    try:
        await _next_named(stream, "unread")
        await publish_board(redis_cache, Board.HARVESTER)
        await publish_board(redis_cache, Board.KNOWLEDGE)
        event, _ = await _next_frame(stream)
        assert event == "ping", "admin-board nudges must not leak to a member"
    finally:
        await stream.aclose()


async def test_agents_nudge_fans_out_to_owner_and_admin(
    db_session: AsyncSession, db_engine: AsyncEngine, redis_cache: Redis
):
    member = await create_user(db_session)
    admin = await create_user(db_session, role=str(UserRole.ADMIN))
    request = fake_request(db_engine, redis_cache)
    owner_stream = _event_stream(request, member.id, str(UserRole.MEMBER))
    admin_stream = _event_stream(request, admin.id, str(UserRole.ADMIN))
    try:
        await _next_named(owner_stream, "unread")
        await _next_named(admin_stream, "unread")
        await publish_board(redis_cache, Board.AGENTS, user_id=member.id)
        assert await _next_named(owner_stream, "board") == {"board": "agents"}
        assert await _next_named(admin_stream, "board") == {"board": "agents"}
    finally:
        await owner_stream.aclose()
        await admin_stream.aclose()


async def test_a_burst_coalesces_with_a_trailing_frame(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    redis_cache: Redis,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(stream_module, "BOARD_EMIT_MIN_SECONDS", 0.3)
    admin = await create_user(db_session, role=str(UserRole.ADMIN))
    stream = _event_stream(fake_request(db_engine, redis_cache), admin.id, str(UserRole.ADMIN))
    try:
        await _next_named(stream, "unread")
        await publish_board(redis_cache, Board.KNOWLEDGE)
        first = await _next_named(stream, "board")
        assert first == {"board": "knowledge"}, "the first nudge emits at once"

        # A hot loop right after the emit: one trailing frame at gap expiry,
        # not one frame per publish.
        loop = asyncio.get_event_loop()
        for _ in range(5):
            await publish_board(redis_cache, Board.KNOWLEDGE)
        started = loop.time()
        second = await _next_named(stream, "board")
        assert second == {"board": "knowledge"}
        assert loop.time() - started >= 0.15, "the trailing frame waits out the emit gap"

        # Nothing left pending: the next frame is a heartbeat, not a board.
        monkeypatch.setattr(stream_module, "HEARTBEAT_SECONDS", 0.05)
        event, _ = await _next_frame(stream)
        assert event == "ping"
    finally:
        await stream.aclose()


async def test_bell_nudge_emits_a_fresh_counter(
    db_session: AsyncSession, db_engine: AsyncEngine, redis_cache: Redis
):
    member = await create_user(db_session)
    stream = _event_stream(fake_request(db_engine, redis_cache), member.id, str(UserRole.MEMBER))
    try:
        assert await _next_named(stream, "unread") == {"count": 0}
        await dispatcher.notify(
            db_session,
            event="agent.run_failed",
            target_user_id=member.id,
            params={"agent_name": "W"},
        )
        await db_session.commit()
        await redis_cache.publish(dispatcher.PUSH_CHANNEL.format(user_id=member.id), "1")
        assert await _next_named(stream, "unread") == {"count": 1}
    finally:
        await stream.aclose()


async def test_route_survives_the_pool_release_rollback(
    db_session: AsyncSession, db_engine: AsyncEngine, redis_cache: Redis
):
    """The route rolls back to free the pool; that expires the ORM user, and a
    lazy `user.id` refresh inside the generator would die with MissingGreenlet."""
    member = await create_user(db_session)
    response = stream_module.events_stream(fake_request(db_engine, redis_cache), member, db_session)
    body = (await response).body_iterator
    try:
        first = await asyncio.wait_for(anext(body), FRAME_TIMEOUT)
        assert str(first).startswith("event: hello")
    finally:
        await body.aclose()  # type: ignore[union-attr]


async def test_stream_requires_auth(client: AsyncClient):
    response = await client.get("/api/v1/events/stream")
    assert response.status_code == 401


async def test_publish_board_survives_a_broken_redis():
    broken = Redis.from_url("redis://127.0.0.1:1", decode_responses=True)
    try:
        await publish_board(broken, Board.KNOWLEDGE)  # must not raise
        await publish_board(broken, Board.AGENTS, user_id=1)
    finally:
        await broken.aclose()
