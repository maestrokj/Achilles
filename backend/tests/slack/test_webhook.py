"""Inbound webhook: challenge, signature gate, DM filter, dedup, fail modes (API)."""

import json
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from redis.asyncio import Redis
from redis.exceptions import RedisError
from saq import Queue
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.config import Settings
from achilles.infra.worker.base import Lane
from achilles.slack.routes import events as events_module
from tests.slack.conftest import BOT_USER, TEAM, configure_slack, sign

pytestmark = [pytest.mark.api, pytest.mark.p1]

URL = "/api/v1/slack/events"


def _dm_event(
    *,
    event_id: str = "Ev001",
    user: str = "U777",
    text: str = "what is the plan?",
    channel_type: str = "im",
    thread_ts: str | None = None,
    **event_extra: object,
) -> bytes:
    event: dict[str, object] = {
        "type": "message",
        "channel": "D42",
        "channel_type": channel_type,
        "user": user,
        "text": text,
        "ts": "1720000000.000100",
        **event_extra,
    }
    if thread_ts is not None:
        event["thread_ts"] = thread_ts
    return json.dumps(
        {"type": "event_callback", "team_id": TEAM, "event_id": event_id, "event": event}
    ).encode()


def _now_ts() -> str:
    return str(int(datetime.now(UTC).timestamp()))


@pytest.fixture
async def interactive_queue(test_settings: Settings):
    queue = Queue.from_url(test_settings.redis_durable_url, name=str(Lane.INTERACTIVE))
    await queue.connect()
    yield queue
    await queue.disconnect()


async def test_not_configured_answers_silent_200(client: AsyncClient):
    resp = await client.post(URL, content=_dm_event(), headers={"Content-Type": "application/json"})
    assert resp.status_code == 200
    assert resp.json() == {}


async def test_invalid_signature_is_401(
    client: AsyncClient, db_session: AsyncSession, test_settings: Settings
):
    await configure_slack(db_session, test_settings)
    body = _dm_event()
    headers = sign(body, timestamp=_now_ts(), secret="wrong-secret")
    resp = await client.post(URL, content=body, headers=headers)
    assert resp.status_code == 401
    assert resp.json()["code"] == "SLACK_SIGNATURE_INVALID"


@pytest.mark.parametrize(
    "event",
    ["hello", [], 42],  # "event" present but not an object
)
async def test_malformed_event_field_answers_silent_200(
    client: AsyncClient,
    db_session: AsyncSession,
    test_settings: Settings,
    interactive_queue: Queue,
    event: object,
):
    # Past the signature gate an event_callback can still carry a non-object
    # "event"; .get() on a str would 500. It must ack silently, enqueue nothing.
    await configure_slack(db_session, test_settings)
    body = json.dumps(
        {"type": "event_callback", "team_id": TEAM, "event_id": "Ev-bad", "event": event}
    ).encode()
    resp = await client.post(URL, content=body, headers=sign(body, timestamp=_now_ts()))
    assert resp.status_code == 200
    assert resp.json() == {}
    assert await interactive_queue.count("queued") == 0


async def test_url_verification_answers_challenge(
    client: AsyncClient, db_session: AsyncSession, test_settings: Settings
):
    await configure_slack(db_session, test_settings)
    body = json.dumps({"type": "url_verification", "challenge": "ch-42", "team_id": TEAM}).encode()
    resp = await client.post(URL, content=body, headers=sign(body, timestamp=_now_ts()))
    assert resp.status_code == 200
    assert resp.json() == {"challenge": "ch-42"}


async def test_url_verification_works_before_the_first_test_probe(
    client: AsyncClient, db_session: AsyncSession, test_settings: Settings
):
    # Token + secret + enabled, but no successful probe yet (team NULL): Slack's
    # URL handshake must still complete, else the admin can never wire the hook.
    await configure_slack(db_session, test_settings, probed=False)
    body = json.dumps({"type": "url_verification", "challenge": "ch-99", "team_id": TEAM}).encode()
    resp = await client.post(URL, content=body, headers=sign(body, timestamp=_now_ts()))
    assert resp.status_code == 200
    assert resp.json() == {"challenge": "ch-99"}


async def test_non_object_json_body_is_silent_200(
    client: AsyncClient, db_session: AsyncSession, test_settings: Settings
):
    # Valid JSON that is not an object must not crash the anonymous endpoint.
    await configure_slack(db_session, test_settings)
    body = b"[]"
    resp = await client.post(URL, content=body, headers=sign(body, timestamp=_now_ts()))
    assert resp.status_code == 200
    assert resp.json() == {}


async def test_bad_signature_does_not_spend_the_workspace_budget(
    client: AsyncClient,
    db_session: AsyncSession,
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
):
    # Rate limit runs after signature: an unsigned flood cannot lock out the
    # workspace, so a genuine signed event still gets through afterwards.
    await configure_slack(db_session, test_settings)
    monkeypatch.setattr(events_module, "WEBHOOK_RATE_LIMIT", 1)
    body = _dm_event(event_id="Ev-legit")
    forged = sign(body, timestamp=_now_ts(), secret="wrong-secret")
    for _ in range(3):
        assert (await client.post(URL, content=body, headers=forged)).status_code == 401
    genuine = await client.post(URL, content=body, headers=sign(body, timestamp=_now_ts()))
    assert genuine.status_code == 200


async def test_dm_event_enqueues_and_a_retry_does_not_double(
    client: AsyncClient,
    db_session: AsyncSession,
    test_settings: Settings,
    redis_durable: Redis,
    interactive_queue: Queue,
):
    await configure_slack(db_session, test_settings)
    body = _dm_event(event_id="Ev777")
    headers = sign(body, timestamp=_now_ts())

    first = await client.post(URL, content=body, headers=headers)
    second = await client.post(URL, content=body, headers=headers)  # Slack retry
    assert first.status_code == second.status_code == 200
    assert await redis_durable.exists("dedup:job:slack-event-Ev777")
    assert await interactive_queue.count("queued") == 1


@pytest.mark.parametrize(
    "body",
    [
        _dm_event(channel_type="channel"),  # not a DM
        _dm_event(subtype="message_changed"),  # edits carry a subtype
        _dm_event(bot_id="B1"),  # another bot
        _dm_event(user=BOT_USER),  # the bot's own post
    ],
)
async def test_non_dm_noise_is_dropped(
    client: AsyncClient,
    db_session: AsyncSession,
    test_settings: Settings,
    interactive_queue: Queue,
    body: bytes,
):
    await configure_slack(db_session, test_settings)
    resp = await client.post(URL, content=body, headers=sign(body, timestamp=_now_ts()))
    assert resp.status_code == 200
    assert await interactive_queue.count("queued") == 0


async def test_rate_limit_answers_429(
    client: AsyncClient,
    db_session: AsyncSession,
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
):
    await configure_slack(db_session, test_settings)
    monkeypatch.setattr(events_module, "WEBHOOK_RATE_LIMIT", 1)
    body = _dm_event(event_id="Ev1")
    headers = sign(body, timestamp=_now_ts())
    assert (await client.post(URL, content=body, headers=headers)).status_code == 200
    resp = await client.post(URL, content=body, headers=headers)
    assert resp.status_code == 429


async def test_redis_outage_fails_closed_503(
    client: AsyncClient,
    db_session: AsyncSession,
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
):
    await configure_slack(db_session, test_settings)

    async def broken(*args: object, **kwargs: object) -> None:
        raise RedisError("gone")

    monkeypatch.setattr(events_module, "hit_sliding_window", broken)
    body = _dm_event()
    resp = await client.post(URL, content=body, headers=sign(body, timestamp=_now_ts()))
    assert resp.status_code == 503
    assert resp.json()["code"] == "SLACK_HOOK_UNAVAILABLE"
