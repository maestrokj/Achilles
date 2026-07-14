"""POST /telegram/webhook — the public inbound webhook (anonymous, secret-gated).

Ack now: before the answer there is only Redis (fail-closed window + enqueue
dedup) and one settings read; the dialogue runs on the interactive lane.
Telegram retries an unacked update — enqueue dedup keyed by update_id absorbs
that. While the surface is off (is_available=false) the hook answers an empty
200 and stays silent. Auth is a secret token in a header, not an HMAC signature:
Achilles generated that secret at setWebhook, so only Telegram carries it.
"""

import hmac
import json
import logging
from datetime import UTC, datetime
from typing import cast

from fastapi import APIRouter, Request
from redis.exceptions import RedisError

from achilles.api.background import publish_lane
from achilles.api.problems import ApiError, rate_limited
from achilles.auth.dependencies import CryptoKey
from achilles.auth.security.crypto import decrypt
from achilles.db.dependencies import DbSession
from achilles.infra.rate_limit import hit_sliding_window
from achilles.infra.redis import PREFIX_RATE_LIMIT
from achilles.infra.worker.base import Lane
from achilles.messenger.constants import WEBHOOK_RATE_LIMIT, WEBHOOK_RATE_WINDOW_SECONDS
from achilles.telegram.constants import (
    CODE_TELEGRAM_HOOK_UNAVAILABLE,
    CODE_TELEGRAM_SECRET_INVALID,
    INBOUND_JOB,
    SECRET_HEADER,
)
from achilles.telegram.service import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telegram", tags=["telegram"])

_ACK: dict[str, object] = {}


@router.post("/webhook")
async def telegram_webhook(
    request: Request, session: DbSession, key: CryptoKey
) -> dict[str, object]:
    raw_body = await request.body()
    try:
        parsed = json.loads(raw_body)
    except ValueError:
        return _ACK  # not JSON — nothing Telegram would retry for
    if not isinstance(parsed, dict):
        return _ACK  # valid JSON but not an update object
    payload = cast("dict[str, object]", parsed)

    now = datetime.now(UTC)

    # The secret is required to authenticate anything and the toggle is the
    # master switch; gate on those two before any work.
    row = await get_settings(session)
    if not row.enabled or row.webhook_secret_enc is None:
        return _ACK  # silent no-op: the surface is off, retries must not pile up

    # Authenticate before spending any budget: the secret is a constant only
    # Telegram (and the admin) hold, so a request reaching the rate limiter is
    # already trusted, and the per-chat key can't be forged into a flood.
    provided = request.headers.get(SECRET_HEADER, "")
    expected = decrypt(row.webhook_secret_enc, key=key)
    if not hmac.compare_digest(provided, expected):
        raise ApiError(
            401, CODE_TELEGRAM_SECRET_INVALID, "Unauthorized", "secret token check failed"
        )

    # Coerce, don't cast: cast() is a no-op at runtime, so a well-formed update
    # object whose "message"/"chat" is *not* a dict (a string, a list) would slip
    # a str through and blow up on .get() with a 500. _as_dict keeps it a 200 ack.
    message = _as_dict(payload.get("message"))
    chat = _as_dict(message.get("chat"))
    chat_id = str(chat.get("id") or "unknown")

    if not row.is_available:
        return _ACK  # configured but not fully wired yet — stay silent

    # Drop non-DM / bot / empty updates before spending a rate-limit slot — this
    # structural check is free (no I/O) and keeps discarded traffic off the window.
    if not _is_inbound_dm(payload, message, chat):
        return _ACK

    # Fail-closed sliding window per chat — only genuine inbound DMs reach it.
    try:
        decision = await hit_sliding_window(
            request.state.redis.durable,
            f"{PREFIX_RATE_LIMIT}hook:telegram:{chat_id}",
            limit=WEBHOOK_RATE_LIMIT,
            window_seconds=WEBHOOK_RATE_WINDOW_SECONDS,
            now=now.timestamp(),
        )
    except RedisError as exc:
        raise ApiError(
            503, CODE_TELEGRAM_HOOK_UNAVAILABLE, "Service Unavailable", "try again shortly"
        ) from exc
    if not decision.allowed:
        raise rate_limited(decision.retry_after, "Telegram webhook rate limit exceeded")

    update_id = str(payload.get("update_id") or "")
    sender = _as_dict(message.get("from"))
    await publish_lane(
        request,
        Lane.INTERACTIVE,
        INBOUND_JOB,
        job_id=f"telegram-update-{update_id}",  # enqueue dedup: a Telegram retry is a no-op
        chat_id=chat_id,
        tg_user=str(sender.get("id") or ""),
        text=str(message.get("text") or ""),
    )
    return _ACK


def _as_dict(value: object) -> dict[str, object]:
    """Telegram fields we treat as objects — tolerate a non-dict as empty."""
    return cast("dict[str, object]", value) if isinstance(value, dict) else {}


def _is_inbound_dm(
    payload: dict[str, object], message: dict[str, object], chat: dict[str, object]
) -> bool:
    """v1 listens to plain human DMs only; the bot's own posts are dropped."""
    sender = _as_dict(message.get("from"))
    return (
        bool(payload.get("update_id"))
        and chat.get("type") == "private"
        and bool(message.get("text"))
        and bool(sender.get("id"))
        and not sender.get("is_bot")
    )
