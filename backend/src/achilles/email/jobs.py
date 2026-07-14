"""SAQ jobs: queued transactional letters (invite · password reset).

Same shape as the other worker jobs: the process opens its own connections and
re-derives the crypto key. A transient SMTP failure raises so SAQ retries with
backoff; a permanent verdict (5xx) logs and finishes — retries are pointless.
The reset job carries the whole flow (lookup → token → send) so the request
path stays timing-uniform (anti-enumeration, delivery.html#errors).
"""

import logging
from datetime import UTC, datetime

import sqlalchemy as sa
from saq.types import Context

from achilles.auth.constants import RESET_TOKEN_TTL, AuditResult, UserStatus
from achilles.auth.models import User
from achilles.auth.services import passwords
from achilles.auth.services.audit import AuditAction, record
from achilles.config import settings as app_settings
from achilles.db.connections import close_connections, create_connections
from achilles.email import service, smtp
from achilles.email.compose import compose
from achilles.email.constants import (
    SMTP_SEND_TIMEOUT_SECONDS,
    EmailKind,
    PermanentSendError,
)
from achilles.email.i18n import role_name

logger = logging.getLogger(__name__)


def invite_link(token: str) -> str:
    return app_settings.public_url(f"/invite/{token}")


def reset_link(token: str) -> str:
    return app_settings.public_url(f"/reset-password/{token}")


async def send_invite_email(
    ctx: Context, *, to: str, token: str, role: str, inviter_name: str, ttl_hours: int
) -> None:
    """One invite letter; the org-default language (the invitee has no profile)."""
    del ctx
    crypto_key = app_settings.derived_crypto_key()
    db = create_connections(app_settings)
    try:
        async with db.pg_session_factory() as session:
            row = await service.get_settings(session)
            if not row.is_available:
                logger.warning("invite email to %s dropped: SMTP switched off", to)
                return
            locale, branding = await service.letter_context(session)
            composed = compose(
                EmailKind.INVITE,
                locale=locale,
                branding=branding,
                params={
                    "inviter_name": inviter_name,
                    "role_name": role_name(role, locale),
                    "ttl_hours": str(ttl_hours),
                },
                action_url=invite_link(token),
            )
            try:
                await smtp.send(
                    row,
                    key=crypto_key,
                    to=to,
                    composed=composed,
                    send_timeout=SMTP_SEND_TIMEOUT_SECONDS,
                )
            except PermanentSendError as exc:
                logger.warning("invite email to %s permanently refused: %s", to, exc)
    finally:
        await close_connections(db)


async def send_password_reset(ctx: Context, *, email: str) -> None:
    """The whole forgot flow off the request path: lookup → token → letter.

    Silence is deliberate: no user, SMTP off, or a refused mailbox all end in
    the log/audit only — the HTTP answer was already uniform.
    """
    del ctx
    crypto_key = app_settings.derived_crypto_key()
    db = create_connections(app_settings)
    try:
        async with db.pg_session_factory() as session:
            row = await service.get_settings(session)
            if not row.is_available:
                return
            user = await session.scalar(
                sa.select(User).where(
                    sa.func.lower(User.email) == email.lower(),
                    User.status == UserStatus.ACTIVE.value,
                )
            )
            if user is None:
                return
            raw = await passwords.issue_reset_token(session, user, now=datetime.now(UTC))
            await session.commit()
            locale, branding = await service.letter_context(session, user)
            composed = compose(
                EmailKind.RESET,
                locale=locale,
                branding=branding,
                params={"ttl_hours": str(int(RESET_TOKEN_TTL.total_seconds() // 3600))},
                action_url=reset_link(raw),
            )
            await record(
                db.pg_session_factory,
                action=AuditAction.PASSWORD_RESET_REQUEST,
                result=AuditResult.SUCCESS,
                target_type="user",
                target_id=str(user.id),
            )
            try:
                await smtp.send(
                    row,
                    key=crypto_key,
                    to=user.email,
                    composed=composed,
                    send_timeout=SMTP_SEND_TIMEOUT_SECONDS,
                )
            except PermanentSendError as exc:
                logger.warning("reset email to user %s permanently refused: %s", user.id, exc)
                await record(
                    db.pg_session_factory,
                    action=AuditAction.PASSWORD_RESET_REQUEST,
                    result=AuditResult.FAILURE,
                    target_type="user",
                    target_id=str(user.id),
                    meta={"error": "delivery_refused"},
                )
    finally:
        await close_connections(db)
