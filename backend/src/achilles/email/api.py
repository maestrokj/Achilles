"""The queue-side face of the letters: job names, lanes and job_id conventions.

Consumers (auth routes) call one function; how a letter becomes a job —
which lane, which dedup shape — is this module's contract, not theirs.
"""

from datetime import UTC, datetime

from fastapi import Request

from achilles.api.background import publish_lane
from achilles.auth.constants import INVITE_TOKEN_TTL
from achilles.auth.models import InviteToken
from achilles.auth.security.tokens import hash_token
from achilles.auth.services.brute_force import hash_email
from achilles.email.constants import SEND_RETRY_JOB_ARGS
from achilles.infra.worker.base import Lane

_INVITE_TTL_HOURS = int(INVITE_TOKEN_TTL.total_seconds() // 3600)


async def queue_invite(
    request: Request, *, lane: Lane, raw: str, row: InviteToken, inviter_name: str
) -> None:
    """One letter = one job; a rotated token yields a fresh job_id."""
    await publish_lane(
        request,
        lane,
        "send_invite_email",
        job_id=f"email:invite:{row.id}:{hash_token(raw)[:12]}",
        to=row.email,
        token=raw,
        role=row.role,
        inviter_name=inviter_name,
        ttl_hours=_INVITE_TTL_HOURS,
        **SEND_RETRY_JOB_ARGS,
    )


async def queue_password_reset(request: Request, *, email: str) -> None:
    """The whole reset flow runs in the worker (anti-enumeration).

    Second-grain job_id: a double-click dedups, a deliberate resend goes through.
    """
    await publish_lane(
        request,
        Lane.INTERACTIVE,
        "send_password_reset",
        job_id=f"email:reset:{hash_email(email)}:{int(datetime.now(UTC).timestamp())}",
        email=email,
        **SEND_RETRY_JOB_ARGS,
    )
