"""mattermost_settings singleton access, patch, live probe and enable gate."""

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import sqlalchemy as sa
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.auth.security.crypto import decrypt, encrypt_optional, mask_encrypted
from achilles.mattermost.client import MattermostApiError, MattermostClient
from achilles.mattermost.constants import (
    CODE_MATTERMOST_ENABLE_FAILED,
    LISTENER_STATUS_KEY,
    TEST_ERR_NETWORK,
    TEST_ERR_NO_BASE_URL,
    TEST_ERR_NO_TOKEN,
)
from achilles.mattermost.models import MattermostSettings
from achilles.mattermost.schemas import (
    MattermostSettingsOut,
    MattermostSettingsPatch,
    MattermostTestOut,
)

logger = logging.getLogger(__name__)

SINGLETON_ID = 1


async def listener_connected(cache: Redis) -> bool | None:
    """The listener's own word on delivery; an expired key reads as unknown."""
    try:
        raw = await cache.get(LISTENER_STATUS_KEY)
    except RedisError:
        # The status line is garnish on the settings card — a Redis hiccup must
        # not turn the whole settings read into a 500.
        logger.warning("Mattermost listener status unavailable", exc_info=True)
        return None
    if raw is None:
        return None
    try:
        return bool(json.loads(raw).get("connected"))
    except ValueError, AttributeError:
        return None


async def get_settings(session: AsyncSession) -> MattermostSettings:
    row = await session.scalar(
        sa.select(MattermostSettings).where(MattermostSettings.id == SINGLETON_ID)
    )
    if row is None:  # pragma: no cover — the migration seeds the row
        msg = "mattermost_settings singleton missing; run migrations"
        raise RuntimeError(msg)
    return row


def settings_out(
    row: MattermostSettings, *, key: bytes, listener_connected: bool | None
) -> MattermostSettingsOut:
    return MattermostSettingsOut(
        enabled=row.enabled,
        base_url=row.base_url,
        bot_username=row.bot_username,
        bot_token_mask=mask_encrypted(row.bot_token_enc, key=key),
        listener_connected=listener_connected,
        last_test_ok=row.last_test_ok,
        last_test_at=row.last_test_at,
    )


def apply_patch(row: MattermostSettings, body: MattermostSettingsPatch, *, key: bytes) -> None:
    fields = body.model_fields_set
    changed_target = False
    if "base_url" in fields:
        changed_target = changed_target or body.base_url != row.base_url
        row.base_url = body.base_url
    if "bot_token" in fields:
        changed_target = True
        row.bot_token_enc = encrypt_optional(body.bot_token, key=key)
    if changed_target:
        # The stamped identity belongs to the old server/token pair; a stale
        # bot_user_id would let the listener run against the wrong identity.
        # The next successful probe (enable or test) re-stamps it.
        row.bot_user_id = None
        row.bot_username = None
    if "enabled" in fields and body.enabled is not None:
        row.enabled = body.enabled


async def run_test(
    session: AsyncSession, row: MattermostSettings, *, key: bytes
) -> MattermostTestOut:
    """Live probe of the token against the server; stamps last_test_*, never a 5xx.

    ``last_test_ok`` vouches for the token and the server being reachable;
    *delivery* health is the listener's connected flag — the card shows both.
    """
    out = await _probe(row, key=key)
    row.last_test_ok = out.ok
    row.last_test_at = datetime.now(UTC)
    await session.commit()
    return out


async def _probe(row: MattermostSettings, *, key: bytes) -> MattermostTestOut:
    """GET /users/me; a success stamps the bot's identity onto the row."""
    if not row.base_url:
        return MattermostTestOut(ok=False, error=TEST_ERR_NO_BASE_URL)
    if not row.bot_token_enc:
        return MattermostTestOut(ok=False, error=TEST_ERR_NO_TOKEN)
    client = MattermostClient(row.base_url, decrypt(row.bot_token_enc, key=key))
    try:
        me = await client.get_me()
    except (MattermostApiError, httpx.HTTPError) as exc:
        error = exc.message if isinstance(exc, MattermostApiError) else TEST_ERR_NETWORK
        return MattermostTestOut(ok=False, error=error)
    finally:
        await client.aclose()
    user_id = me.get("id")
    username = me.get("username")
    row.bot_user_id = str(user_id) if user_id else row.bot_user_id
    row.bot_username = str(username) if username else row.bot_username
    return MattermostTestOut(ok=True, bot_username=row.bot_username)


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of an enable attempt: whether it succeeded and, if not, why.

    ``probed`` is True only when /users/me actually answered — an enable with
    no token or address yet is a benign no-op (ok, not probed) rather than a
    failure: the switch may stand, availability stays derived.
    """

    ok: bool
    probed: bool = False
    error_code: str | None = None
    detail: str = ""


def stamp_connect(row: MattermostSettings, result: ProbeResult) -> None:
    """Fold an enable outcome into the row's test state, mirroring run_test.

    A failed probe rolls the switch back — no live token behind it — and stamps
    a failed test; a real probe stamps success. A configless enable (ok but
    nothing probed) keeps the switch and stamps nothing.
    """
    if not result.ok:
        row.enabled = False
        row.last_test_ok = False
        row.last_test_at = datetime.now(UTC)
    elif result.probed:
        row.last_test_ok = True
        row.last_test_at = datetime.now(UTC)


async def connect_probe(row: MattermostSettings, *, key: bytes) -> ProbeResult:
    """The enable gate: the token must answer /users/me before the switch stays on.

    Mutates the row on success (bot_user_id, bot_username) but does not commit
    or stamp last_test_* — the caller owns the transaction so enabling is
    atomic: no live token, no enabled bot.
    """
    if not row.base_url or not row.bot_token_enc:
        return ProbeResult(ok=True)  # nothing to probe; the switch may still stand
    out = await _probe(row, key=key)
    if not out.ok:
        logger.warning("mattermost enable probe failed: %s", out.error)
        return ProbeResult(
            ok=False, error_code=CODE_MATTERMOST_ENABLE_FAILED, detail=out.error or ""
        )
    return ProbeResult(ok=True, probed=True)
