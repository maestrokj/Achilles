"""POST /slack/events — the public inbound webhook (anonymous, signature-gated).

Ack < 3 s: before the answer there is only Redis (fail-closed window + enqueue
dedup) and one settings read; the dialogue runs on the interactive lane. Slack
retries on timeouts — enqueue_idempotent keyed by event_id absorbs them. While
the surface is not configured (is_available=false) the hook answers an empty
200 and stays silent, so Slack's retry queue never piles up.
"""

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
from achilles.slack import signature
from achilles.slack.constants import (
    CODE_SLACK_HOOK_UNAVAILABLE,
    CODE_SLACK_SIGNATURE_INVALID,
    INBOUND_JOB,
)
from achilles.slack.service import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/slack", tags=["slack"])

_ACK: dict[str, object] = {}


@router.post("/events")
async def slack_events(request: Request, session: DbSession, key: CryptoKey) -> dict[str, object]:
    raw_body = await request.body()
    try:
        parsed = json.loads(raw_body)
    except ValueError:
        return _ACK  # not JSON — nothing Slack would retry for
    if not isinstance(parsed, dict):
        return _ACK  # valid JSON but not an event object — Slack never sends this
    payload = cast("dict[str, object]", parsed)

    now = datetime.now(UTC)

    # A signature is required to verify anything, and the toggle is the master
    # switch; a probe-stamped `team` is not needed to complete Slack's URL
    # handshake, so gate on those two alone (not full is_available).
    row = await get_settings(session)
    if not row.enabled or row.signing_secret_enc is None:
        return _ACK  # silent no-op: the surface is off, retries must not pile up

    # Authenticate before spending any per-workspace budget: the rate-limit key
    # is derived from the request body, so consuming it pre-signature would let
    # an anonymous flood lock a real workspace out (rate-limit.html#consumers).
    valid = signature.verify(
        decrypt(row.signing_secret_enc, key=key),
        timestamp=request.headers.get("X-Slack-Request-Timestamp", ""),
        body=raw_body,
        signature=request.headers.get("X-Slack-Signature", ""),
        now=now.timestamp(),
    )
    if not valid:
        raise ApiError(401, CODE_SLACK_SIGNATURE_INVALID, "Unauthorized", "signature check failed")

    # Fail-closed sliding window per (now signature-verified) workspace.
    team_id = str(payload.get("team_id") or "unknown")
    try:
        decision = await hit_sliding_window(
            request.state.redis.durable,
            f"{PREFIX_RATE_LIMIT}hook:slack:{team_id}",
            limit=WEBHOOK_RATE_LIMIT,
            window_seconds=WEBHOOK_RATE_WINDOW_SECONDS,
            now=now.timestamp(),
        )
    except RedisError as exc:
        raise ApiError(
            503, CODE_SLACK_HOOK_UNAVAILABLE, "Service Unavailable", "try again shortly"
        ) from exc
    if not decision.allowed:
        raise rate_limited(decision.retry_after, "Slack webhook rate limit exceeded")

    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge")}

    if not row.is_available:
        return _ACK  # configured but no successful test probe yet — stay silent

    # Coerce, don't cast: cast() is a no-op at runtime, so an event_callback whose
    # "event" is not an object (a string, a list) would slip a str through and 500
    # on .get() below. _as_dict keeps a malformed payload a silent 200 ack.
    event = _as_dict(payload.get("event"))
    if not _is_inbound_dm(payload, event, bot_user_id=row.bot_user_id):
        return _ACK

    event_id = str(payload.get("event_id") or "")
    await publish_lane(
        request,
        Lane.INTERACTIVE,
        INBOUND_JOB,
        job_id=f"slack-event-{event_id}",  # enqueue dedup: a Slack retry is a no-op
        team=team_id,
        channel=str(event.get("channel") or ""),
        slack_user=str(event.get("user") or ""),
        text=str(event.get("text") or ""),
        ts=str(event.get("ts") or ""),
        thread_ts=str(event["thread_ts"]) if event.get("thread_ts") else None,
    )
    return _ACK


def _as_dict(value: object) -> dict[str, object]:
    """Slack fields we treat as objects — tolerate a non-dict as empty."""
    return cast("dict[str, object]", value) if isinstance(value, dict) else {}


def _is_inbound_dm(
    payload: dict[str, object], event: dict[str, object], *, bot_user_id: str | None
) -> bool:
    """v1 listens to plain human DMs only; the bot's own posts are dropped."""
    return (
        payload.get("type") == "event_callback"
        and event.get("type") == "message"
        and event.get("channel_type") == "im"
        and not event.get("subtype")
        and not event.get("bot_id")
        and bool(event.get("user"))
        and event.get("user") != bot_user_id
        and bool(payload.get("event_id"))
    )
