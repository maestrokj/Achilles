"""Mattermost settings routes: the #mattermost section of the Platform screen.

Read is Owner+Admin, write is Owner (the Settings zone lock); secrets go out
only as masks. The test probe runs live and stamps last_test_* — a failed probe
is a 200 with ok=false, not a 5xx. Turning the bot on is atomic: the token must
answer /users/me and only then does the switch stay on; a refused or unreachable
probe rolls the switch back and answers with a problem, so the admin never sees
a green switch with a dead token behind it. Turning off needs no remote call —
the singleton listener notices on its next settings poll and hangs up.
"""

from fastapi import APIRouter, Request

from achilles.admin.dependencies import SettingsManager, SettingsReader
from achilles.api.problems import ApiError
from achilles.auth.constants import AuditResult
from achilles.auth.dependencies import CryptoKey
from achilles.auth.routes.common import record_audit
from achilles.auth.services.audit import AuditAction
from achilles.db.dependencies import DbSession
from achilles.mattermost import service
from achilles.mattermost.schemas import (
    MattermostSettingsOut,
    MattermostSettingsPatch,
    MattermostTestOut,
)

router = APIRouter(prefix="/admin/mattermost", tags=["admin-mattermost"])


def _enable_problem(result: service.ProbeResult) -> ApiError:
    """Turn a failed enable probe into a problem+json the admin section can act on."""
    return ApiError(
        409,
        result.error_code or "",
        "Mattermost bot could not be enabled",
        f"The Mattermost server refused the token check: {result.detail}",
    )


@router.get("")
async def get_mattermost_settings(
    user: SettingsReader, request: Request, session: DbSession, key: CryptoKey
) -> MattermostSettingsOut:
    del user
    row = await service.get_settings(session)
    connected = await service.listener_connected(request.state.redis.cache)
    return service.settings_out(row, key=key, listener_connected=connected)


@router.patch("")
async def patch_mattermost_settings(
    user: SettingsManager,
    request: Request,
    session: DbSession,
    key: CryptoKey,
    body: MattermostSettingsPatch,
) -> MattermostSettingsOut:
    row = await service.get_settings(session)
    was_enabled = row.enabled
    service.apply_patch(row, body, key=key)
    # Gate on the resulting state: a running bot re-proves its token whenever
    # it's freshly switched on or the token/server it points at changed.
    target_changed = bool({"bot_token", "base_url"} & body.model_fields_set)
    turning_on = row.enabled and (not was_enabled or target_changed)

    connect: service.ProbeResult | None = None
    if turning_on:
        # A failed probe rolls the switch back rather than lie green.
        connect = await service.connect_probe(row, key=key)
        service.stamp_connect(row, connect)
    await session.commit()

    problem = _enable_problem(connect) if connect is not None and not connect.ok else None
    await record_audit(
        request,
        action=AuditAction.SETTINGS_UPDATE,
        result=AuditResult.FAILURE if problem else AuditResult.SUCCESS,
        actor_id=user.id,
        target_type="mattermost_settings",
        target_id="1",
        meta={"fields": sorted(body.model_fields_set)},
    )
    if problem:
        raise problem
    connected = await service.listener_connected(request.state.redis.cache)
    return service.settings_out(row, key=key, listener_connected=connected)


@router.post("/test")
async def test_mattermost_connection(
    user: SettingsManager, session: DbSession, key: CryptoKey
) -> MattermostTestOut:
    del user
    row = await service.get_settings(session)
    return await service.run_test(session, row, key=key)
