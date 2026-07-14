"""Slack settings routes: the #slack section of the Platform screen.

Read is Owner+Admin, write is Owner (the Settings zone lock); secrets go out
only as masks. The test probe runs live and stamps last_test_* — a failed
probe is a 200 with ok=false, not a 5xx (the harvester test-connection shape).
"""

from fastapi import APIRouter, Request

from achilles.admin.dependencies import SettingsManager, SettingsReader
from achilles.auth.dependencies import CryptoKey
from achilles.auth.routes.common import record_audit
from achilles.auth.services.audit import AuditAction
from achilles.db.dependencies import DbSession
from achilles.slack import service
from achilles.slack.schemas import SlackSettingsOut, SlackSettingsPatch, SlackTestOut

router = APIRouter(prefix="/admin/slack", tags=["admin-slack"])


@router.get("")
async def get_slack_settings(
    user: SettingsReader, session: DbSession, key: CryptoKey
) -> SlackSettingsOut:
    del user
    row = await service.get_settings(session)
    return service.settings_out(row, key=key)


@router.patch("")
async def patch_slack_settings(
    user: SettingsManager,
    request: Request,
    session: DbSession,
    key: CryptoKey,
    body: SlackSettingsPatch,
) -> SlackSettingsOut:
    row = await service.get_settings(session)
    service.apply_patch(row, body, key=key)
    await session.commit()
    await record_audit(
        request,
        action=AuditAction.SETTINGS_UPDATE,
        actor_id=user.id,
        target_type="slack_settings",
        target_id="1",
        meta={"fields": sorted(body.model_fields_set)},
    )
    return service.settings_out(row, key=key)


@router.post("/test")
async def test_slack_connection(
    user: SettingsManager, session: DbSession, key: CryptoKey
) -> SlackTestOut:
    del user
    row = await service.get_settings(session)
    return await service.run_test(session, row, key=key)
