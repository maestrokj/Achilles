"""The singleton WebSocket listener: Mattermost's substitute for an inbound webhook.

Mattermost has no HTTP push for DMs, so the bot dials out: one long-lived
connection to /api/v4/websocket (hosted in the scheduler process — the only
1-replica service, so exactly one listener exists) receives `posted` events and
enqueues them on the interactive lane. The listener only transports: filtering
is structural, identity and dialogue live in the job. Nothing is exposed
publicly and no webhook secret exists — the bearer token authenticates the dial.

Lifecycle: a supervisor polls mattermost_settings every LISTENER_POLL_SECONDS;
while available it holds the socket, and a watchdog closes it as soon as the
switch goes off or the config it was dialled with changes (that is how a PATCH
lands without a signal channel). Failures reconnect with exponential backoff.
Health is a TTL'd Redis key the admin card reads — an expired key honestly
means "not running".
"""

import asyncio
import json
import logging
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

import websockets
from redis.asyncio import Redis, RedisError
from saq.types import Context

from achilles.auth.security.crypto import decrypt
from achilles.config import settings as app_settings
from achilles.db.connections import DbConnections, close_connections, create_connections
from achilles.infra.rate_limit import hit_sliding_window
from achilles.infra.redis import (
    PREFIX_RATE_LIMIT,
    RedisPools,
    close_redis_pools,
    create_redis_pools,
)
from achilles.infra.worker.base import Lane, publish
from achilles.mattermost.constants import (
    API_PATH,
    INBOUND_JOB,
    LISTENER_BACKOFF_MAX_SECONDS,
    LISTENER_BACKOFF_START_SECONDS,
    LISTENER_HEALTHY_RESET_SECONDS,
    LISTENER_POLL_SECONDS,
    LISTENER_STATUS_KEY,
    LISTENER_STATUS_TTL_SECONDS,
)
from achilles.mattermost.service import get_settings
from achilles.messenger.constants import WEBHOOK_RATE_LIMIT, WEBHOOK_RATE_WINDOW_SECONDS

logger = logging.getLogger(__name__)

_TASK_KEY = "mattermost_listener"


@dataclass(frozen=True, slots=True)
class ListenerConfig:
    """The snapshot the socket was dialled with; any change hangs up."""

    base_url: str
    token: str
    bot_user_id: str

    @property
    def ws_url(self) -> str:
        scheme, netloc, path, _, _ = urlsplit(self.base_url)
        ws_scheme = "wss" if scheme == "https" else "ws"
        return urlunsplit((ws_scheme, netloc, f"{path}{API_PATH}/websocket", "", ""))


def auth_frame(token: str) -> str:
    """The first frame out: Mattermost's post-connect authentication challenge."""
    return json.dumps({"seq": 1, "action": "authentication_challenge", "data": {"token": token}})


def inbound_post(frame: object, *, bot_user_id: str) -> dict[str, object] | None:
    """The parsed post when the frame is a human DM worth a job; None otherwise.

    Pure and structural: `posted` event, a DM channel, a plain message (no
    system subtype), a real author who is not the bot itself and not another
    bot/webhook. The `post` field arrives JSON-encoded inside the JSON frame.
    """
    if not isinstance(frame, dict) or frame.get("event") != "posted":
        return None
    data = frame.get("data")
    if not isinstance(data, dict) or data.get("channel_type") != "D":
        return None
    raw_post = data.get("post")
    if not isinstance(raw_post, str):
        return None
    try:
        post = json.loads(raw_post)
    except ValueError:
        return None
    if not isinstance(post, dict):
        return None
    if not post.get("id") or not post.get("channel_id"):
        return None
    user_id = post.get("user_id")
    if not user_id or user_id == bot_user_id:
        return None
    if post.get("type"):  # joined/left/pinned/… — system messages carry a type
        return None
    message = post.get("message")
    if not isinstance(message, str) or not message.strip():
        return None
    props = post.get("props")
    if isinstance(props, dict) and (props.get("from_bot") == "true" or props.get("from_webhook")):
        return None
    return post


# --- lifecycle hooks (infra/scheduler/settings.py) ---


async def listener_startup(ctx: Context) -> None:
    ctx[_TASK_KEY] = asyncio.create_task(supervise())  # type: ignore[literal-required]


async def listener_shutdown(ctx: Context) -> None:
    task = ctx.get(_TASK_KEY)  # type: ignore[call-overload]
    if isinstance(task, asyncio.Task):
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


# --- the supervisor ---


async def supervise() -> None:
    """Hold one socket while the bot is available; reconnect with backoff."""
    crypto_key = app_settings.derived_crypto_key()
    db = create_connections(app_settings)
    redis = create_redis_pools(app_settings)
    backoff = LISTENER_BACKOFF_START_SECONDS
    try:
        while True:
            config = await _load_config(db, crypto_key=crypto_key)
            if config is None:
                await _write_status(redis.cache, connected=False)
                await asyncio.sleep(LISTENER_POLL_SECONDS)
                continue
            dialled_at = time.monotonic()
            try:
                await _run_socket(config, db=db, redis=redis, crypto_key=crypto_key)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # whatever dropped, the listener survives
                logger.warning("mattermost listener dropped: %s", exc)
                await _write_status(redis.cache, connected=False, error=str(exc))
                if time.monotonic() - dialled_at >= LISTENER_HEALTHY_RESET_SECONDS:
                    backoff = LISTENER_BACKOFF_START_SECONDS
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, LISTENER_BACKOFF_MAX_SECONDS)
            else:
                # A deliberate hang-up (switched off / config changed): re-read
                # the settings right away, no penalty.
                backoff = LISTENER_BACKOFF_START_SECONDS
    finally:
        await close_redis_pools(redis)
        await close_connections(db)


async def _load_config(db: DbConnections, *, crypto_key: bytes) -> ListenerConfig | None:
    async with db.pg_session_factory() as session:
        row = await get_settings(session)
        if not row.is_available or not row.base_url or not row.bot_token_enc:
            return None
        return ListenerConfig(
            base_url=row.base_url,
            token=decrypt(row.bot_token_enc, key=crypto_key),
            bot_user_id=row.bot_user_id or "",
        )


async def _run_socket(
    config: ListenerConfig, *, db: DbConnections, redis: RedisPools, crypto_key: bytes
) -> None:
    async with websockets.connect(config.ws_url) as ws:
        await ws.send(auth_frame(config.token))
        await _write_status(redis.cache, connected=True)
        watchdog = asyncio.create_task(
            _watch_config(ws, config, db=db, cache=redis.cache, crypto_key=crypto_key)
        )
        watchdog.add_done_callback(_log_watchdog_crash)
        try:
            async for raw in ws:
                await _write_status(redis.cache, connected=True)
                try:
                    frame = json.loads(raw)
                except ValueError:
                    continue
                post = inbound_post(frame, bot_user_id=config.bot_user_id)
                if post is None:
                    continue
                await _enqueue(redis, post)
        finally:
            watchdog.cancel()
            with suppress(asyncio.CancelledError):
                await watchdog


async def _watch_config(
    ws: websockets.ClientConnection,
    config: ListenerConfig,
    *,
    db: DbConnections,
    cache: Redis,
    crypto_key: bytes,
) -> None:
    """Hang up as soon as the settings no longer match the dialled snapshot.

    Doubles as the idle heartbeat: a quiet DM channel sends no frames, so the
    status TTL is renewed here, not only per event.
    """
    while True:
        await asyncio.sleep(LISTENER_POLL_SECONDS)
        current = await _load_config(db, crypto_key=crypto_key)
        if current != config:
            await ws.close()
            return
        await _write_status(cache, connected=True)


def _log_watchdog_crash(task: asyncio.Task[None]) -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.warning("mattermost config watchdog crashed: %s", task.exception())


async def _enqueue(redis: RedisPools, post: dict[str, object]) -> None:
    channel_id = str(post["channel_id"])
    # Fail-closed per-channel window: on a Redis hiccup the event is dropped —
    # a lost message is recoverable (the person asks again), a stampede is not.
    try:
        decision = await hit_sliding_window(
            redis.durable,
            f"{PREFIX_RATE_LIMIT}hook:mattermost:{channel_id}",
            limit=WEBHOOK_RATE_LIMIT,
            window_seconds=WEBHOOK_RATE_WINDOW_SECONDS,
            now=time.time(),
        )
    except RedisError as exc:
        logger.warning("mattermost rate-limit unavailable, dropping event: %s", exc)
        return
    if not decision.allowed:
        logger.warning("mattermost channel %s over the inbound window", channel_id)
        return
    # job_id = post id: reconnect replays and duplicate frames enqueue once.
    await publish(
        app_settings.redis_durable_url,
        redis.durable,
        Lane.INTERACTIVE,
        INBOUND_JOB,
        job_id=f"mattermost-post-{post['id']}",
        channel_id=channel_id,
        mm_user=str(post["user_id"]),
        text=str(post["message"]),
        post_id=str(post["id"]),
        root_id=str(post["root_id"]) if post.get("root_id") else None,
    )


async def _write_status(cache: Redis, *, connected: bool, error: str | None = None) -> None:
    payload = json.dumps(
        {"connected": connected, "error": error, "at": datetime.now(UTC).isoformat()}
    )
    with suppress(RedisError):
        await cache.set(LISTENER_STATUS_KEY, payload, ex=LISTENER_STATUS_TTL_SECONDS)
