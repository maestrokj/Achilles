"""GET /events/stream — the one multiplexed SSE connection per tab.

Frames: `hello` (the caller's board subscriptions — the client invalidates
them all, so a reconnect catches up wholesale), `board` (a nudge naming the
changed board), `unread` (the bell counter, refetched server-side on the bell
nudge), `ping` (heartbeat as a real event frame so the client watchdog sees
liveness). Access is decided at subscribe time: the role picks the channel
set, no per-message filtering.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from achilles.api.sse import HEARTBEAT_SECONDS, SSE_HEADERS, sse_frame
from achilles.auth.constants import Permission, has_permission
from achilles.auth.dependencies import CurrentUser
from achilles.db.dependencies import DbSession
from achilles.events.constants import (
    AGENTS_ADMIN_CHANNEL,
    AGENTS_USER_CHANNEL,
    BOARD_CHANNEL,
    BOARD_EMIT_MIN_SECONDS,
    Board,
)
from achilles.notifications import service as notifications_service
from achilles.notifications.dispatcher import PUSH_CHANNEL

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/events", tags=["events"])


def _subscriptions(user_id: int, role: str) -> dict[str, Board]:
    """Channel → board map for this caller; both agents channels coalesce to one board."""
    channels = {AGENTS_USER_CHANNEL.format(user_id=user_id): Board.AGENTS}
    if has_permission(role, Permission.AI_ADMIN):
        channels[AGENTS_ADMIN_CHANNEL] = Board.AGENTS
    if has_permission(role, Permission.KNOWLEDGE_ADMIN):
        channels[BOARD_CHANNEL.format(board=Board.HARVESTER)] = Board.HARVESTER
        channels[BOARD_CHANNEL.format(board=Board.KNOWLEDGE)] = Board.KNOWLEDGE
    return channels


async def _event_stream(request: Request, user_id: int, role: str) -> AsyncGenerator[str]:
    redis = request.state.redis.cache
    session_factory = request.state.db.pg_session_factory
    subscriptions = _subscriptions(user_id, role)
    bell_channel = PUSH_CHANNEL.format(user_id=user_id)
    pubsub = redis.pubsub()

    pending: set[Board] = set()
    bell_pending = False
    last_emit: dict[Board, float] = {}
    last_frame = time.monotonic()

    def next_timeout() -> float:
        now = time.monotonic()
        deadlines = [last_frame + HEARTBEAT_SECONDS]
        deadlines.extend(last_emit.get(board, 0.0) + BOARD_EMIT_MIN_SECONDS for board in pending)
        return max(0.0, min(deadlines) - now)

    try:
        # subscribe() checks a pool connection out for the stream's lifetime, so
        # it must sit under this finally: a cancellation mid-subscribe (client
        # gone while Redis is slow) would otherwise strand the slot until the
        # process restarts — 20 such strands starved the whole cache pool live.
        await pubsub.subscribe(bell_channel, *subscriptions)
        boards = sorted({board.value for board in subscriptions.values()})
        yield sse_frame("hello", {"boards": boards})
        # The opening frame carries the current counter — no first-poll gap.
        async with session_factory() as session:
            count = await notifications_service.unread_count(session, user_id)
        yield sse_frame("unread", {"count": count})
        # A client that went away cancels the generator at the next yield —
        # no is_disconnected() poll (it deadlocks under buffered transports).
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=next_timeout()
            )
            # Drain the burst — one fan-out must not become N refetches.
            while message is not None:
                if message["channel"] == bell_channel:
                    bell_pending = True
                else:
                    board = subscriptions.get(message["channel"])
                    if board is not None:
                        pending.add(board)
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0)

            now = time.monotonic()
            emitted = False
            if bell_pending:
                bell_pending = False
                async with session_factory() as session:
                    count = await notifications_service.unread_count(session, user_id)
                yield sse_frame("unread", {"count": count})
                emitted = True
            for board in sorted(pending):
                if now - last_emit.get(board, 0.0) >= BOARD_EMIT_MIN_SECONDS:
                    pending.discard(board)
                    last_emit[board] = now
                    yield sse_frame("board", {"board": board.value})
                    emitted = True
            if emitted:
                last_frame = now
            elif now - last_frame >= HEARTBEAT_SECONDS:
                yield sse_frame("ping", {})
                last_frame = now
    except asyncio.CancelledError:
        raise  # the client went away — normal shutdown
    finally:
        with contextlib.suppress(Exception):
            await pubsub.aclose()


@router.get("/stream")
async def events_stream(
    request: Request, user: CurrentUser, session: DbSession
) -> StreamingResponse:
    """One push connection per tab; auth resolves before the stream starts."""
    # The dependency only authenticated us; hand its connection back to the pool
    # NOW — dependency teardown waits for the (endless) stream, and an idle
    # `idle in transaction` connection per open tab would drain the pool.
    # Capture id + role first: rollback expires `user`, and a lazy refresh
    # inside the generator has no session to run on (MissingGreenlet).
    user_id = user.id
    role = user.role
    await session.rollback()
    return StreamingResponse(
        _event_stream(request, user_id, role), media_type="text/event-stream", headers=SSE_HEADERS
    )
