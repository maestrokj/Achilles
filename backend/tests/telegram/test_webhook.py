"""Inbound webhook: secret gate, DM filter, dedup, fail modes (API)."""

import json

import pytest
from httpx import AsyncClient
from redis.asyncio import Redis
from redis.exceptions import RedisError
from saq import Queue
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.config import Settings
from achilles.infra.worker.base import Lane
from achilles.telegram.routes import events as events_module
from tests.telegram.conftest import configure_telegram, secret_headers

pytestmark = [pytest.mark.api, pytest.mark.p1]

URL = "/api/v1/telegram/webhook"


def _message_update(
    *,
    update_id: int = 1,
    user_id: int = 777,
    text: str = "what is the plan?",
    chat_type: str = "private",
    **message_extra: object,
) -> bytes:
    message: dict[str, object] = {
        "message_id": 10,
        "from": {"id": user_id, "is_bot": False, "first_name": "Max"},
        "chat": {"id": user_id, "type": chat_type},
        "text": text,
        **message_extra,
    }
    return json.dumps({"update_id": update_id, "message": message}).encode()


@pytest.fixture
async def interactive_queue(test_settings: Settings):
    queue = Queue.from_url(test_settings.redis_durable_url, name=str(Lane.INTERACTIVE))
    await queue.connect()
    yield queue
    await queue.disconnect()


async def test_not_configured_answers_silent_200(client: AsyncClient):
    resp = await client.post(
        URL, content=_message_update(), headers={"Content-Type": "application/json"}
    )
    assert resp.status_code == 200
    assert resp.json() == {}


async def test_invalid_secret_is_401(
    client: AsyncClient, db_session: AsyncSession, test_settings: Settings
):
    await configure_telegram(db_session, test_settings)
    resp = await client.post(
        URL, content=_message_update(), headers=secret_headers(secret="wrong-secret")
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "TELEGRAM_SECRET_INVALID"


async def test_non_object_json_body_is_silent_200(
    client: AsyncClient, db_session: AsyncSession, test_settings: Settings
):
    await configure_telegram(db_session, test_settings)
    resp = await client.post(URL, content=b"[]", headers=secret_headers())
    assert resp.status_code == 200
    assert resp.json() == {}


@pytest.mark.parametrize(
    "body",
    [
        b'{"update_id": 4, "message": "hello"}',  # message is a string, not an object
        b'{"update_id": 5, "message": {"chat": "nope", "text": "hi"}}',  # chat not an object
        b'{"update_id": 6, "message": {"chat": {"id": 1, "type": "private"}, "from": []}}',
        b'{"update_id": 7, "message": []}',  # message is a list
    ],
)
async def test_malformed_nested_fields_answer_silent_200(
    client: AsyncClient,
    db_session: AsyncSession,
    test_settings: Settings,
    interactive_queue: Queue,
    body: bytes,
):
    # Past the secret gate a well-formed envelope can still carry a non-object
    # "message"/"chat"/"from"; .get() on a str would 500. It must ack silently.
    await configure_telegram(db_session, test_settings)
    resp = await client.post(URL, content=body, headers=secret_headers())
    assert resp.status_code == 200
    assert resp.json() == {}
    assert await interactive_queue.count("queued") == 0


async def test_bad_secret_does_not_spend_the_chat_budget(
    client: AsyncClient,
    db_session: AsyncSession,
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
):
    # Rate limit runs after the secret check: a wrong-secret flood cannot lock out
    # a chat, so a genuine authenticated update still gets through afterwards.
    await configure_telegram(db_session, test_settings)
    monkeypatch.setattr(events_module, "WEBHOOK_RATE_LIMIT", 1)
    body = _message_update()
    for _ in range(3):
        forged = await client.post(URL, content=body, headers=secret_headers(secret="nope"))
        assert forged.status_code == 401
    genuine = await client.post(URL, content=body, headers=secret_headers())
    assert genuine.status_code == 200


async def test_configured_but_unavailable_is_silent(
    client: AsyncClient,
    db_session: AsyncSession,
    test_settings: Settings,
    interactive_queue: Queue,
):
    # Secret + enabled but no bot token: the secret gate passes, is_available is
    # false, so the update is acked silently and nothing is queued.
    await configure_telegram(db_session, test_settings, available=False)
    resp = await client.post(URL, content=_message_update(), headers=secret_headers())
    assert resp.status_code == 200
    assert await interactive_queue.count("queued") == 0


async def test_update_enqueues_and_a_retry_does_not_double(
    client: AsyncClient,
    db_session: AsyncSession,
    test_settings: Settings,
    redis_durable: Redis,
    interactive_queue: Queue,
):
    await configure_telegram(db_session, test_settings)
    body = _message_update(update_id=555)

    first = await client.post(URL, content=body, headers=secret_headers())
    second = await client.post(URL, content=body, headers=secret_headers())  # Telegram retry
    assert first.status_code == second.status_code == 200
    assert await redis_durable.exists("dedup:job:telegram-update-555")
    assert await interactive_queue.count("queued") == 1


@pytest.mark.parametrize(
    "body",
    [
        _message_update(chat_type="group"),  # not a DM
        _message_update(text=""),  # no text (empty string is falsy)
        json.dumps(  # a bot's own message
            {
                "update_id": 2,
                "message": {
                    "message_id": 1,
                    "from": {"id": 9, "is_bot": True},
                    "chat": {"id": 9, "type": "private"},
                    "text": "hi",
                },
            }
        ).encode(),
        json.dumps({"update_id": 3, "edited_message": {"text": "x"}}).encode(),  # not a message
    ],
)
async def test_non_dm_noise_is_dropped(
    client: AsyncClient,
    db_session: AsyncSession,
    test_settings: Settings,
    interactive_queue: Queue,
    body: bytes,
):
    await configure_telegram(db_session, test_settings)
    resp = await client.post(URL, content=body, headers=secret_headers())
    assert resp.status_code == 200
    assert await interactive_queue.count("queued") == 0


async def test_rate_limit_answers_429(
    client: AsyncClient,
    db_session: AsyncSession,
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
):
    await configure_telegram(db_session, test_settings)
    monkeypatch.setattr(events_module, "WEBHOOK_RATE_LIMIT", 1)
    body = _message_update()
    assert (await client.post(URL, content=body, headers=secret_headers())).status_code == 200
    resp = await client.post(URL, content=body, headers=secret_headers())
    assert resp.status_code == 429


async def test_redis_outage_fails_closed_503(
    client: AsyncClient,
    db_session: AsyncSession,
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
):
    await configure_telegram(db_session, test_settings)

    async def broken(*args: object, **kwargs: object) -> None:
        raise RedisError("gone")

    monkeypatch.setattr(events_module, "hit_sliding_window", broken)
    resp = await client.post(URL, content=_message_update(), headers=secret_headers())
    assert resp.status_code == 503
    assert resp.json()["code"] == "TELEGRAM_HOOK_UNAVAILABLE"
