"""Platform settings routes: admin GET/PATCH + the anonymous branding read.

Only GET and PATCH exist for the singleton — it is never created or deleted
over the wire (405 for the rest comes free). Read is Owner+Admin, write is
Owner only (the Settings zone of the sidebar carries the lock).
"""

from fastapi import APIRouter, Request

from achilles.admin import service
from achilles.admin.dependencies import SettingsManager, SettingsReader
from achilles.admin.schemas import BrandingOut, PlatformSettingsOut, PlatformSettingsPatch
from achilles.auth.routes.common import record_audit, redis_durable
from achilles.auth.services.audit import AuditAction
from achilles.db.dependencies import DbSession
from achilles.email import service as email_service
from achilles.knowledge_store.services import platform

router = APIRouter(prefix="/admin/settings", tags=["admin-settings"])
public_router = APIRouter(prefix="/platform", tags=["platform"])


@router.get("")
async def get_settings(user: SettingsReader, session: DbSession) -> PlatformSettingsOut:
    del user
    row = await platform.get_platform_settings(session)
    smtp_configured = await email_service.smtp_available(session)
    return service.settings_out(row, smtp_configured=smtp_configured)


@router.patch("")
async def patch_settings(
    user: SettingsManager,
    request: Request,
    session: DbSession,
    body: PlatformSettingsPatch,
) -> PlatformSettingsOut:
    row = await service.apply_patch(session, redis_durable(request), body)
    await session.commit()
    await record_audit(
        request,
        action=AuditAction.SETTINGS_UPDATE,
        actor_id=user.id,
        target_type="platform_settings",
        target_id="1",
        meta={"fields": sorted(body.model_fields_set)},
    )
    smtp_configured = await email_service.smtp_available(session)
    return service.settings_out(row, smtp_configured=smtp_configured)


@public_router.get("/branding")
async def get_branding(session: DbSession) -> BrandingOut:
    """The anonymous slice for the login screen and shell chrome — no secrets here."""
    row = await platform.get_platform_settings(session)
    return BrandingOut.model_validate(row)
