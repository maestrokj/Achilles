"""Identity and access dependencies: Bearer → user, permission gate, ownership.

Regular routes trust the ≤15-min token window for role/status (design trade-off);
the must_change_password gate is enforced server-side on every request except
change-password and logout (routing.html, authentication.html#temp-password-gate).
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Request, Response
from fastapi import params as fastapi_params
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.admin import maintenance as admin_maintenance
from achilles.api import API_V1
from achilles.api.csrf import MUTATING_METHODS
from achilles.api.problems import CODE_FORBIDDEN, ApiError
from achilles.api.rate_limit import enforce_identity_rate_limit
from achilles.auth.constants import (
    API_KEY_PREFIX,
    API_KEY_RATE_LIMIT_RPM,
    CODE_ACCOUNT_DEACTIVATED,
    CODE_PASSWORD_CHANGE_REQUIRED,
    REFRESH_COOKIE_NAME,
    Permission,
    UserRole,
    UserStatus,
    has_permission,
)
from achilles.auth.models import ApiKey, User
from achilles.auth.security.jwt import (
    TokenExpiredError,
    TokenInvalidError,
    decode_access_token,
)
from achilles.auth.services import api_keys
from achilles.auth.services.sessions import current_family, token_expired, token_invalid
from achilles.config import Settings
from achilles.db.dependencies import get_session
from achilles.infra.redis import PREFIX_RATE_LIMIT

# While must_change_password is set, only these paths answer (plus login/refresh,
# which are anonymous and carry the flag to the client).
PASSWORD_GATE_EXEMPT_PATHS = frozenset(
    {
        f"{API_V1}/auth/password/change",
        f"{API_V1}/auth/logout",
    }
)

_BEARER_PREFIX = "Bearer "


def _bearer_token(request: Request) -> str:
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith(_BEARER_PREFIX):
        raise token_invalid()
    return authorization[len(_BEARER_PREFIX) :]


def extract_api_key(request: Request) -> str:
    """Key-only gate for the external surfaces (Public API, MCP).

    The Bearer must carry an ``ach_`` key — JWT never crosses those surfaces.
    """
    token = _bearer_token(request)
    if not token.startswith(API_KEY_PREFIX):
        raise token_invalid()
    return token


def _rpm_for_role(settings: Settings, role: str) -> int:
    if role == UserRole.OWNER.value:
        return settings.api_rate_limit_rpm_owner
    if role == UserRole.ADMIN.value:
        return settings.api_rate_limit_rpm_admin
    return settings.api_rate_limit_rpm_member


async def resolve_key_request(
    request: Request,
    response: Response,
    session: AsyncSession,
    raw_key: str,
    now: datetime,
    *,
    allow_mutating: bool = False,
) -> tuple[ApiKey, User]:
    """Key-identity core shared by /api/v1 and the external surfaces (Public API, MCP).

    Resolve → method gate → one rate-limit bucket per key across all surfaces →
    touch → maintenance. External surfaces pass ``allow_mutating=True`` because
    their contract is read-only by construction (POST-as-read search).
    """
    key, user = await api_keys.resolve_key_identity(session, raw_key, now=now)
    if not allow_mutating and request.method in MUTATING_METHODS:
        # Key scope is read-only in v1; writes are Agent Engine territory.
        raise ApiError(403, CODE_FORBIDDEN, "Forbidden", "API keys are read-only")
    await enforce_identity_rate_limit(
        request.state.redis.durable,
        bucket_key=f"{PREFIX_RATE_LIMIT}key:{key.id}",
        rpm=API_KEY_RATE_LIMIT_RPM,
        now=now.timestamp(),
        response=response,
    )
    # Only after the throttle: refused requests must not cost a write.
    if await api_keys.touch_last_used(session, key.id, now=now):
        await session.commit()
    await admin_maintenance.ensure_member_access(request, user)
    return key, user


async def get_current_user(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    settings: Settings = request.app.state.settings
    token = _bearer_token(request)
    now = datetime.now(UTC)

    if token.startswith(API_KEY_PREFIX):
        _, user = await resolve_key_request(request, response, session, token, now)
        return user

    try:
        claims = decode_access_token(token, secret=settings.secret_key)
    except TokenExpiredError as exc:
        raise token_expired() from exc
    except TokenInvalidError as exc:
        raise token_invalid() from exc

    # Throttle on the claims alone — a rejected request must not cost a DB query
    # (the ≤15-min role snapshot is the documented trade-off).
    await enforce_identity_rate_limit(
        request.state.redis.durable,
        bucket_key=f"{PREFIX_RATE_LIMIT}user:{claims.user_id}",
        rpm=_rpm_for_role(settings, claims.role),
        now=now.timestamp(),
        response=response,
    )

    user = await session.get(User, claims.user_id)
    if user is None:
        raise token_invalid()

    if user.must_change_password and request.url.path not in PASSWORD_GATE_EXEMPT_PATHS:
        raise ApiError(
            403,
            CODE_PASSWORD_CHANGE_REQUIRED,
            "Forbidden",
            "Password change required before any other action",
        )

    # Org maintenance mode: members wait it out, Owner/Admin keep working
    # (login/refresh are anonymous and stay open — someone must switch it off).
    await admin_maintenance.ensure_member_access(request, user)

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_current_family(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> uuid.UUID | None:
    """The refresh-token family behind the caller's cookie, if it is still live."""
    return await current_family(session, raw_token=request.cookies.get(REFRESH_COOKIE_NAME))


CurrentFamily = Annotated[uuid.UUID | None, Depends(get_current_family)]


def require(permission: Permission) -> fastapi_params.Depends:
    """RBAC gate: the endpoint declares a permission, roles map to permissions.

    Critical checks read role *and* status from the DB (authentication.html:614):
    a permissioned action is refused the instant an account is deactivated. Plain
    ``CurrentUser`` endpoints stay on the deliberate ≤15-min stateless window —
    instant access-token invalidation everywhere is v2 (jti blacklist).
    """

    async def check(user: CurrentUser) -> User:
        if user.status != UserStatus.ACTIVE.value:
            raise ApiError(403, CODE_ACCOUNT_DEACTIVATED, "Forbidden", "Account is deactivated")
        if not has_permission(user.role, permission):
            raise ApiError(403, CODE_FORBIDDEN, "Forbidden", "Insufficient permissions")
        return user

    return Depends(check)


def ensure_owns(user: User, owner_id: int) -> None:
    """IDOR guard: permission alone is not enough for personal resources."""
    if user.id != owner_id:
        raise ApiError(403, CODE_FORBIDDEN, "Forbidden", "Not the owner of this resource")


def get_crypto_key(request: Request) -> bytes:
    """Data-encryption key for *_enc columns; derived once in create_app."""
    key: bytes = request.app.state.crypto_key
    return key


CryptoKey = Annotated[bytes, Depends(get_crypto_key)]
