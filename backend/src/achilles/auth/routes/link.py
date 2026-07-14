"""Messenger-link routes: code issue (web side) + confirm (bot side).

The Slack bot consumes the service directly (slack/jobs.py — same process, no
HTTP hop); this confirm endpoint stays as the wire contract for out-of-process
consumers. TODO(seam): Telegram bot (v2).
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel, EmailStr

from achilles.api.security_headers import SensitiveResponse
from achilles.auth.constants import LINK_CODE_TTL
from achilles.auth.dependencies import CurrentUser
from achilles.auth.routes.common import record_audit, redis_durable
from achilles.auth.services import messenger_link
from achilles.auth.services.audit import AuditAction
from achilles.db.dependencies import DbSession

# The code IS the credential — link responses are never cached, referrer never leaks.
router = APIRouter(prefix="/link", tags=["link"], dependencies=[SensitiveResponse])


class LinkCodeOut(BaseModel):
    code: str
    expires_in_seconds: int


class LinkConfirmIn(BaseModel):
    code: str
    platform_user_id: str
    platform_email: EmailStr | None = None


class LinkConfirmOut(BaseModel):
    user_id: int
    status: str = "linked"


@router.post("/{platform}", status_code=201)
async def issue_link_code(
    platform: str, user: CurrentUser, request: Request, session: DbSession
) -> LinkCodeOut:
    """The user gets a short-lived code in the web app and hands it to the bot in DM."""
    messenger_link.validate_platform(platform)
    now = datetime.now(UTC)
    raw = await messenger_link.issue_code(session, user=user, now=now)
    await session.commit()
    await record_audit(
        request,
        action=AuditAction.LINK_CREATE,
        actor_id=user.id,
        target_type="link",
        target_id=platform,
    )
    return LinkCodeOut(code=raw, expires_in_seconds=int(LINK_CODE_TTL.total_seconds()))


@router.post("/{platform}/confirm")
async def confirm_link(
    platform: str, body: LinkConfirmIn, request: Request, session: DbSession
) -> LinkConfirmOut:
    """Bot side: returns the code → binds the chat identity to the account."""
    messenger_link.validate_platform(platform)
    now = datetime.now(UTC)
    await messenger_link.guard_chat_attempts(
        redis_durable(request), platform=platform, chat_id=body.platform_user_id, now=now
    )
    user = await messenger_link.confirm_code(
        session,
        raw_code=body.code,
        platform=platform,
        platform_user_id=body.platform_user_id,
        platform_email=body.platform_email,
        now=now,
    )
    await session.commit()
    await record_audit(
        request,
        action=AuditAction.LINK_CONFIRM,
        actor_id=user.id,
        target_type="identity_mapping",
        target_id=f"{platform}:{body.platform_user_id}",
    )
    return LinkConfirmOut(user_id=user.id)
