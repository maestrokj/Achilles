"""Audit journal writer — the single write point (protection.html#audit-log).

Every entry commits in its own transaction so a rolled-back request still leaves
its trace. Secrets never enter `meta`.
"""

from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from achilles.auth.constants import AuditResult
from achilles.auth.models import AuditLog, User


class AuditAction(StrEnum):
    SETUP = "auth.setup"
    LOGIN = "auth.login"
    LOGOUT = "auth.logout"
    LOGOUT_ALL = "auth.logout_all"
    SESSION_REVOKE = "auth.session_revoke"
    SESSIONS_REVOKE_OTHERS = "auth.sessions_revoke_others"
    REFRESH_REUSE = "auth.refresh_reuse_detected"
    PASSWORD_CHANGE = "password.change"  # noqa: S105 — audit action name
    PASSWORD_RESET_REQUEST = "password.reset_request"  # noqa: S105
    PASSWORD_RESET = "password.reset"  # noqa: S105
    USER_ROLE_CHANGE = "user.role_change"
    USER_STATUS_CHANGE = "user.status_change"
    USER_EMAIL_CHANGE = "user.email_change"
    USER_PROFILE_UPDATE = "user.profile_update"
    USER_DELETE = "user.delete"
    USER_SESSIONS_TERMINATE = "user.sessions_terminate"
    USER_EXPORT = "user.export"
    INVITE_CREATE = "invite.create"
    INVITE_ACCEPT = "invite.accept"
    API_KEY_CREATE = "api_key.create"
    API_KEY_RENAME = "api_key.rename"
    API_KEY_REVOKE = "api_key.revoke"
    LINK_CREATE = "link.create"
    LINK_CONFIRM = "link.confirm"
    KNOWLEDGE_REINDEX = "knowledge.reindex"
    KNOWLEDGE_CURATION_CANCEL = "knowledge.curation_cancel"
    KNOWLEDGE_BACKUP = "knowledge.backup"
    KNOWLEDGE_BACKUP_SETTINGS_UPDATE = "knowledge.backup_settings_update"
    KNOWLEDGE_RESTORE = "knowledge.restore"
    SOURCE_CREATE = "source.create"
    SOURCE_UPDATE = "source.update"
    SOURCE_DELETE = "source.delete"
    SOURCE_SYNC_START = "source.sync_start"
    SOURCE_SYNC_CANCEL = "source.sync_cancel"
    AI_PROVIDER_CREATE = "ai.provider_create"
    AI_PROVIDER_UPDATE = "ai.provider_update"
    AI_PROVIDER_DELETE = "ai.provider_delete"
    AI_MODEL_CREATE = "ai.model_create"
    AI_MODEL_UPDATE = "ai.model_update"
    AI_MODEL_DELETE = "ai.model_delete"
    AI_ASSIGNMENT_CHANGE = "ai.assignment_change"
    AI_TOOL_CREATE = "ai.tool_create"
    AI_TOOL_UPDATE = "ai.tool_update"
    AI_TOOL_DELETE = "ai.tool_delete"
    AI_PROMPT_UPDATE = "ai.prompt_update"
    AGENT_PAUSE = "agent.pause"
    AGENT_LIMITS_UPDATE = "agent.limits_update"
    SETTINGS_UPDATE = "settings.update"
    INVITE_RESEND = "invite.resend"
    INVITE_REVOKE = "invite.revoke"
    IDENTITY_LINK = "identity.link"
    IDENTITY_UNLINK = "identity.unlink"


# Audit action groups (audit-log.html facet): group name → action prefixes.
# Lives next to AuditAction so a new action family cannot silently miss the map.
AUDIT_ACTION_GROUPS: dict[str, tuple[str, ...]] = {
    "auth": ("auth.", "password."),
    "admin": ("user.", "invite.", "settings.", "knowledge.", "source.", "identity."),
    "api-keys": ("api_key.",),
    "ai": ("ai.", "agent."),
}


async def record(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    action: AuditAction,
    result: AuditResult,
    actor_id: int | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    async with session_factory() as session, session.begin():
        # Snapshot the actor's email now: the journal outlives the actor, so a
        # later deletion must not erase who acted. System events (no actor) skip it.
        actor_email = (
            await session.scalar(sa.select(User.email).where(User.id == actor_id))
            if actor_id is not None
            else None
        )
        session.add(
            AuditLog(
                actor_id=actor_id,
                actor_email=actor_email,
                action=action.value,
                target_type=target_type,
                target_id=target_id,
                result=result.value,
                ip=ip,
                user_agent=user_agent,
                meta=meta,
            )
        )
