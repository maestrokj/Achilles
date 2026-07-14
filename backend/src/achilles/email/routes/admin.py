"""SMTP settings routes: the #smtp section of the Platform screen.

Read is Owner+Admin, write is Owner (the Settings zone lock); the password goes
out only as a mask. The test sends a real letter to the acting admin inline —
a failed send is a 200 with ok=false, not a 5xx.
"""

from fastapi import APIRouter, Request

from achilles.admin.dependencies import SettingsManager, SettingsReader
from achilles.auth.dependencies import CryptoKey
from achilles.auth.routes.common import record_audit
from achilles.auth.services.audit import AuditAction
from achilles.db.dependencies import DbSession
from achilles.email import service
from achilles.email.schemas import SmtpSettingsOut, SmtpSettingsPatch, SmtpTestOut

router = APIRouter(prefix="/admin/smtp", tags=["admin-smtp"])


@router.get("")
async def get_smtp_settings(
    user: SettingsReader, session: DbSession, key: CryptoKey
) -> SmtpSettingsOut:
    del user
    row = await service.get_settings(session)
    return service.settings_out(row, key=key)


@router.patch("")
async def patch_smtp_settings(
    user: SettingsManager,
    request: Request,
    session: DbSession,
    key: CryptoKey,
    body: SmtpSettingsPatch,
) -> SmtpSettingsOut:
    row = await service.get_settings(session)
    service.apply_patch(row, body, key=key)
    await session.commit()
    await record_audit(
        request,
        action=AuditAction.SETTINGS_UPDATE,
        actor_id=user.id,
        target_type="smtp_settings",
        target_id="1",
        meta={"fields": sorted(body.model_fields_set)},
    )
    return service.settings_out(row, key=key)


@router.post("/test")
async def test_smtp_connection(
    user: SettingsManager, session: DbSession, key: CryptoKey
) -> SmtpTestOut:
    """A real letter to the acting admin, in their language (templates.html#test)."""
    row = await service.get_settings(session)
    locale, branding = await service.letter_context(session, user)
    return await service.run_test(
        session, row, key=key, to=user.email, locale=locale, branding=branding
    )
