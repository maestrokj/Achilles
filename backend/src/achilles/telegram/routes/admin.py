"""Telegram settings routes: the #telegram section of the Platform screen.

Read is Owner+Admin, write is Owner (the Settings zone lock); secrets go out
only as masks. The test probe runs live and stamps last_test_* — a failed probe
is a 200 with ok=false, not a 5xx. Turning the bot on is atomic: it registers
the webhook at Telegram (Achilles owns the generated secret) and only then does
the switch stay on; a registration that Telegram refuses (bad token) or that a
non-public address makes impossible rolls the switch back and answers with a
problem, so the admin never sees a green switch with no delivery behind it.
"""

from fastapi import APIRouter, Request

from achilles.admin.dependencies import SettingsManager, SettingsReader
from achilles.api.problems import ApiError
from achilles.auth.constants import AuditResult
from achilles.auth.dependencies import CryptoKey
from achilles.auth.routes.common import record_audit
from achilles.auth.services.audit import AuditAction
from achilles.db.dependencies import DbSession
from achilles.telegram import service
from achilles.telegram.constants import CODE_TELEGRAM_WEBHOOK_NOT_PUBLIC
from achilles.telegram.schemas import TelegramSettingsOut, TelegramSettingsPatch, TelegramTestOut

router = APIRouter(prefix="/admin/telegram", tags=["admin-telegram"])


def _webhook_problem(result: service.WebhookResult) -> ApiError:
    """Turn a failed connect into a problem+json the admin section can act on."""
    if result.error_code == CODE_TELEGRAM_WEBHOOK_NOT_PUBLIC:
        detail = (
            f"Telegram needs a public HTTPS address to deliver updates, but this "
            f"instance is reachable at {result.detail!r}. Set PUBLIC_BASE_URL to a "
            f"public URL and restart, then enable the bot."
        )
    else:
        detail = f"Telegram refused the webhook: {result.detail}"
    return ApiError(
        409,
        result.error_code or "",
        "Telegram bot could not be enabled",
        detail,
    )


@router.get("")
async def get_telegram_settings(
    user: SettingsReader, session: DbSession, key: CryptoKey
) -> TelegramSettingsOut:
    del user
    row = await service.get_settings(session)
    return service.settings_out(row, key=key)


@router.patch("")
async def patch_telegram_settings(
    user: SettingsManager,
    request: Request,
    session: DbSession,
    key: CryptoKey,
    body: TelegramSettingsPatch,
) -> TelegramSettingsOut:
    row = await service.get_settings(session)
    was_enabled = row.enabled
    service.apply_patch(row, body, key=key)
    # Sync Telegram's webhook with what actually changed: a running bot
    # (re-)registers whenever it's freshly switched on or its token is rotated —
    # setWebhook binds to the new token — and disabling removes the hook. Keying
    # on the resulting state, not merely which fields were present, skips a
    # spurious call on an explicit-null `enabled` or a token patch left off.
    token_rotated = "bot_token" in body.model_fields_set
    turning_on = row.enabled and (not was_enabled or token_rotated)
    turning_off = was_enabled and not row.enabled

    connect: service.WebhookResult | None = None
    if turning_on:
        # A failed connect rolls the switch back rather than lie green.
        connect = await service.connect_webhook(row, key=key)
        service.stamp_connect(row, connect)
    await session.commit()

    if turning_off:
        await service.disconnect_webhook(row, key=key)

    problem = _webhook_problem(connect) if connect is not None and not connect.ok else None
    await record_audit(
        request,
        action=AuditAction.SETTINGS_UPDATE,
        result=AuditResult.FAILURE if problem else AuditResult.SUCCESS,
        actor_id=user.id,
        target_type="telegram_settings",
        target_id="1",
        meta={"fields": sorted(body.model_fields_set)},
    )
    if problem:
        raise problem
    return service.settings_out(row, key=key)


@router.post("/test")
async def test_telegram_connection(
    user: SettingsManager, session: DbSession, key: CryptoKey
) -> TelegramTestOut:
    del user
    row = await service.get_settings(session)
    return await service.run_test(session, row, key=key)
