"""slack_settings singleton access, patch and the live connection probe."""

from datetime import UTC, datetime

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.auth.security.crypto import decrypt, encrypt_optional, mask_encrypted
from achilles.slack.client import SlackApiError, SlackBotClient
from achilles.slack.models import SlackSettings
from achilles.slack.schemas import SlackSettingsOut, SlackSettingsPatch, SlackTestOut

SINGLETON_ID = 1


async def get_settings(session: AsyncSession) -> SlackSettings:
    row = await session.scalar(sa.select(SlackSettings).where(SlackSettings.id == SINGLETON_ID))
    if row is None:  # pragma: no cover — the migration seeds the row
        msg = "slack_settings singleton missing; run migrations"
        raise RuntimeError(msg)
    return row


def settings_out(row: SlackSettings, *, key: bytes) -> SlackSettingsOut:
    return SlackSettingsOut(
        enabled=row.enabled,
        auto_link_by_email=row.auto_link_by_email,
        team=row.team,
        team_name=row.team_name,
        bot_user_id=row.bot_user_id,
        bot_token_mask=mask_encrypted(row.bot_token_enc, key=key),
        signing_secret_set=bool(row.signing_secret_enc),
        last_test_ok=row.last_test_ok,
        last_test_at=row.last_test_at,
    )


def apply_patch(row: SlackSettings, body: SlackSettingsPatch, *, key: bytes) -> None:
    fields = body.model_fields_set
    if "bot_token" in fields:
        row.bot_token_enc = encrypt_optional(body.bot_token, key=key)
    if "signing_secret" in fields:
        row.signing_secret_enc = encrypt_optional(body.signing_secret, key=key)
    if "enabled" in fields and body.enabled is not None:
        row.enabled = body.enabled
    if "auto_link_by_email" in fields and body.auto_link_by_email is not None:
        row.auto_link_by_email = body.auto_link_by_email


async def run_test(session: AsyncSession, row: SlackSettings, *, key: bytes) -> SlackTestOut:
    """Live auth.test: stamps last_test_* and the workspace facts; never a 5xx."""
    out = await _probe(row, key=key)
    if out.ok:
        row.team, row.team_name, row.bot_user_id = out.team, out.team_name, out.bot_user_id
    row.last_test_ok = out.ok
    row.last_test_at = datetime.now(UTC)
    await session.commit()
    return out


async def _probe(row: SlackSettings, *, key: bytes) -> SlackTestOut:
    if not row.bot_token_enc:
        return SlackTestOut(ok=False, error="no_token")
    client = SlackBotClient(decrypt(row.bot_token_enc, key=key))
    try:
        data = await client.auth_test()
    except (SlackApiError, httpx.HTTPError) as exc:
        error = exc.error if isinstance(exc, SlackApiError) else "network_error"
        return SlackTestOut(ok=False, error=error)
    finally:
        await client.aclose()
    return SlackTestOut(
        ok=True,
        team=str(data.get("team_id") or "") or None,
        team_name=str(data.get("team") or "") or None,
        bot_user_id=str(data.get("user_id") or "") or None,
    )
