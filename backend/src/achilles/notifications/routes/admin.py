"""Admin config routes: channels · routing matrix (index.html#api).

Read is Owner+Admin, write is Owner (the Settings zone lock). Webhook URL and
secret travel one way — the wire sees a mask and a flag. Builtins are neither
created nor deleted; the in-app rail cannot be switched off; locked matrix
cells refuse to close (409).
"""

from fastapi import APIRouter, Request, status

from achilles.admin.dependencies import SettingsManager, SettingsReader
from achilles.auth.dependencies import CryptoKey
from achilles.auth.routes.common import record_audit
from achilles.auth.services.audit import AuditAction
from achilles.db.dependencies import DbSession
from achilles.email import service as email_service
from achilles.notifications import service
from achilles.notifications.schemas import (
    ChannelCreate,
    ChannelOut,
    ChannelPatch,
    ChannelsOut,
    ChannelTestOut,
    RoutesOut,
    RoutesPatch,
)

channels_router = APIRouter(prefix="/admin/notification-channels", tags=["admin-notifications"])
routes_router = APIRouter(prefix="/admin/notification-routes", tags=["admin-notifications"])


@channels_router.get("")
async def list_channels(user: SettingsReader, session: DbSession, key: CryptoKey) -> ChannelsOut:
    del user
    rows = await service.list_channels(session)
    return ChannelsOut(items=[service.channel_out(row, key=key) for row in rows])


@channels_router.post("", status_code=status.HTTP_201_CREATED)
async def create_channel(
    user: SettingsManager,
    request: Request,
    session: DbSession,
    key: CryptoKey,
    body: ChannelCreate,
) -> ChannelOut:
    channel = await service.create_webhook(session, body, key=key)
    await record_audit(
        request,
        action=AuditAction.SETTINGS_UPDATE,
        actor_id=user.id,
        target_type="notification_channel",
        target_id=str(channel.id),
        meta={"op": "create", "preset": body.preset},
    )
    return service.channel_out(channel, key=key)


@channels_router.patch("/{channel_id}")
async def patch_channel(
    channel_id: int,
    user: SettingsManager,
    request: Request,
    session: DbSession,
    key: CryptoKey,
    body: ChannelPatch,
) -> ChannelOut:
    channel = await service.get_channel(session, channel_id)
    channel = await service.patch_channel(session, channel, body, key=key)
    await record_audit(
        request,
        action=AuditAction.SETTINGS_UPDATE,
        actor_id=user.id,
        target_type="notification_channel",
        target_id=str(channel.id),
        meta={"fields": sorted(body.model_fields_set)},
    )
    return service.channel_out(channel, key=key)


@channels_router.delete("/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_channel(
    channel_id: int, user: SettingsManager, request: Request, session: DbSession
) -> None:
    channel = await service.get_channel(session, channel_id)
    await service.delete_channel(session, channel)
    await record_audit(
        request,
        action=AuditAction.SETTINGS_UPDATE,
        actor_id=user.id,
        target_type="notification_channel",
        target_id=str(channel_id),
        meta={"op": "delete"},
    )


@channels_router.post("/{channel_id}/test")
async def test_channel(
    channel_id: int, user: SettingsManager, session: DbSession, key: CryptoKey
) -> ChannelTestOut:
    """A fabricated event to the endpoint; a failed POST is 200 + ok=false."""
    del user
    channel = await service.get_channel(session, channel_id)
    locale = await email_service.org_locale(session)
    ok, error = await service.test_channel(session, channel, key=key, locale=locale)
    return ChannelTestOut(ok=ok, error=error)


@routes_router.get("")
async def list_routes(user: SettingsReader, session: DbSession) -> RoutesOut:
    del user
    return RoutesOut(items=await service.list_routes(session))


@routes_router.patch("")
async def patch_routes(
    user: SettingsManager, request: Request, session: DbSession, body: RoutesPatch
) -> RoutesOut:
    items = await service.patch_routes(session, body.items)
    await record_audit(
        request,
        action=AuditAction.SETTINGS_UPDATE,
        actor_id=user.id,
        target_type="notification_routes",
        meta={"cells": len(body.items)},
    )
    return RoutesOut(items=items)
