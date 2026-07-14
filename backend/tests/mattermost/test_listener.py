"""The WebSocket listener: event filtering (pure), the socket loop, the watchdog."""

import asyncio
import json
from typing import Any

import pytest
import sqlalchemy as sa
from redis.asyncio import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.config import Settings
from achilles.db.connections import close_connections, create_connections
from achilles.infra.redis import close_redis_pools, create_redis_pools
from achilles.mattermost import listener, service
from achilles.mattermost.constants import LISTENER_STATUS_KEY
from tests.mattermost.conftest import BASE_URL, BOT_TOKEN, BOT_USER_ID, configure_mattermost

pytestmark = [pytest.mark.integration, pytest.mark.p1]

BOT = "bot-user-1"


def frame(post: dict[str, object], *, channel_type: str = "D", event: str = "posted") -> dict:
    """A Mattermost `posted` frame: the post travels JSON-encoded inside JSON."""
    return {"event": event, "data": {"channel_type": channel_type, "post": json.dumps(post)}}


def post(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "post-1",
        "channel_id": "dm-1",
        "user_id": "person-1",
        "message": "hello",
        "type": "",
        "props": {},
    }
    return {**base, **overrides}


class TestWantsEvent:
    """The structural filter — pure, one clause per rejection."""

    def test_accepts_a_plain_human_dm(self):
        got = listener.inbound_post(frame(post()), bot_user_id=BOT)
        assert got is not None and got["id"] == "post-1"

    def test_rejects_non_posted_events(self):
        assert listener.inbound_post(frame(post(), event="typing"), bot_user_id=BOT) is None

    def test_rejects_public_channels(self):
        assert listener.inbound_post(frame(post(), channel_type="O"), bot_user_id=BOT) is None

    def test_rejects_the_bots_own_posts(self):
        assert listener.inbound_post(frame(post(user_id=BOT)), bot_user_id=BOT) is None

    def test_rejects_system_subtypes(self):
        got = listener.inbound_post(frame(post(type="system_join_channel")), bot_user_id=BOT)
        assert got is None

    def test_rejects_empty_messages(self):
        assert listener.inbound_post(frame(post(message="  ")), bot_user_id=BOT) is None

    def test_rejects_other_bots_and_webhooks(self):
        bot_post = post(props={"from_bot": "true"})
        hook_post = post(props={"from_webhook": "true"})
        assert listener.inbound_post(frame(bot_post), bot_user_id=BOT) is None
        assert listener.inbound_post(frame(hook_post), bot_user_id=BOT) is None

    def test_rejects_malformed_frames(self):
        assert listener.inbound_post("not a dict", bot_user_id=BOT) is None
        assert listener.inbound_post({"event": "posted"}, bot_user_id=BOT) is None
        bad_json = {"event": "posted", "data": {"channel_type": "D", "post": "{oops"}}
        assert listener.inbound_post(bad_json, bot_user_id=BOT) is None


class TestFraming:
    def test_auth_challenge_is_the_first_frame(self):
        sent = json.loads(listener.auth_frame("tok"))
        assert sent == {
            "seq": 1,
            "action": "authentication_challenge",
            "data": {"token": "tok"},
        }

    def test_ws_url_swaps_the_scheme(self):
        https = listener.ListenerConfig(base_url="https://mm.test", token="t", bot_user_id="b")
        http = listener.ListenerConfig(base_url="http://mm.test:8065", token="t", bot_user_id="b")
        assert https.ws_url == "wss://mm.test/api/v4/websocket"
        assert http.ws_url == "ws://mm.test:8065/api/v4/websocket"


class FakeSocket:
    """A websockets.connect stand-in: yields canned frames, records sends."""

    def __init__(self, frames: list[object]) -> None:
        self.frames = frames
        self.sent: list[str] = []
        self.closed = False

    async def __aenter__(self) -> FakeSocket:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def send(self, data: str) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        self.closed = True

    def __aiter__(self) -> Any:
        async def gen():
            for item in self.frames:
                yield json.dumps(item) if not isinstance(item, str) else item

        return gen()


@pytest.fixture
async def wired(db_session: AsyncSession, test_settings: Settings):
    """Configured settings + real DB/Redis handles the socket loop needs."""
    await configure_mattermost(db_session, test_settings)
    db = create_connections(test_settings)
    redis = create_redis_pools(test_settings)
    try:
        yield db, redis
    finally:
        await close_redis_pools(redis)
        await close_connections(db)


async def test_run_socket_authenticates_filters_and_enqueues(
    wired, test_settings: Settings, monkeypatch: pytest.MonkeyPatch
):
    db, redis = wired
    published: list[dict[str, object]] = []

    async def record_publish(*_args: object, **kwargs: object) -> bool:
        published.append(kwargs)
        return True

    monkeypatch.setattr(listener, "publish", record_publish)
    monkeypatch.setattr(listener, "app_settings", test_settings)
    frames = [
        {"event": "hello", "data": {}},
        frame(post(id="post-9", root_id="root-9", message="a question")),
        frame(post(user_id=BOT_USER_ID)),  # the bot's own reply echoes back — dropped
    ]
    socket = FakeSocket(frames)
    monkeypatch.setattr(listener.websockets, "connect", lambda _url: socket)

    config = listener.ListenerConfig(base_url=BASE_URL, token=BOT_TOKEN, bot_user_id=BOT_USER_ID)
    await listener._run_socket(config, db=db, redis=redis, crypto_key=b"k" * 32)

    assert socket.sent and json.loads(socket.sent[0])["action"] == "authentication_challenge"
    (job,) = published
    assert job["job_id"] == "mattermost-post-post-9"
    assert job["channel_id"] == "dm-1"
    assert job["mm_user"] == "person-1"
    assert job["text"] == "a question"
    assert job["post_id"] == "post-9"
    assert job["root_id"] == "root-9"
    # health: the status key says connected while frames flow
    status = await redis.cache.get(LISTENER_STATUS_KEY)
    assert status is not None and json.loads(status)["connected"] is True
    assert await service.listener_connected(redis.cache) is True


async def test_rate_limit_failure_drops_the_event(
    wired, test_settings: Settings, monkeypatch: pytest.MonkeyPatch
):
    # Fail-closed: no Redis verdict, no enqueue — a lost message is recoverable,
    # a stampede is not.
    db, redis = wired
    published: list[dict[str, object]] = []

    async def record_publish(*_args: object, **kwargs: object) -> bool:
        published.append(kwargs)
        return True

    async def broken_window(*_args: object, **_kwargs: object) -> object:
        raise RedisError("down")

    monkeypatch.setattr(listener, "publish", record_publish)
    monkeypatch.setattr(listener, "hit_sliding_window", broken_window)
    monkeypatch.setattr(listener, "app_settings", test_settings)
    socket = FakeSocket([frame(post())])
    monkeypatch.setattr(listener.websockets, "connect", lambda _url: socket)

    config = listener.ListenerConfig(base_url=BASE_URL, token=BOT_TOKEN, bot_user_id=BOT_USER_ID)
    await listener._run_socket(config, db=db, redis=redis, crypto_key=b"k" * 32)

    assert published == []


async def test_watchdog_hangs_up_when_the_config_changes(
    wired, db_session: AsyncSession, test_settings: Settings, monkeypatch: pytest.MonkeyPatch
):
    db, redis = wired
    monkeypatch.setattr(listener, "LISTENER_POLL_SECONDS", 0.01)
    key = test_settings.derived_crypto_key()

    # The socket was dialled with a token that no longer matches the settings row.
    stale = listener.ListenerConfig(base_url=BASE_URL, token="old-token", bot_user_id=BOT_USER_ID)
    socket = FakeSocket([])

    await asyncio.wait_for(
        listener._watch_config(socket, stale, db=db, cache=redis.cache, crypto_key=key),
        timeout=5,
    )
    assert socket.closed is True


async def test_watchdog_hangs_up_when_switched_off(
    wired, db_session: AsyncSession, test_settings: Settings, monkeypatch: pytest.MonkeyPatch
):
    db, redis = wired
    monkeypatch.setattr(listener, "LISTENER_POLL_SECONDS", 0.01)
    key = test_settings.derived_crypto_key()
    current = listener.ListenerConfig(base_url=BASE_URL, token=BOT_TOKEN, bot_user_id=BOT_USER_ID)
    socket = FakeSocket([])

    watchdog = asyncio.create_task(
        listener._watch_config(socket, current, db=db, cache=redis.cache, crypto_key=key)
    )
    await asyncio.sleep(0.05)
    assert socket.closed is False  # config still matches — the socket stays up

    await db_session.execute(sa.text("UPDATE mattermost_settings SET enabled = false WHERE id=1"))
    await db_session.commit()
    await asyncio.wait_for(watchdog, timeout=5)
    assert socket.closed is True


async def test_listener_connected_reads_unknown_when_the_key_expired(wired):
    _db, redis = wired
    assert await service.listener_connected(redis.cache) is None
