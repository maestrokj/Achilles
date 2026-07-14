"""Self-service profile: read the account + its catalogues, edit name and region.

Only the user themselves may read/write here (CurrentUser); email and role are
admin territory. Design: auth-security/_wireframes/profile-account.html.
"""

from fastapi import APIRouter, Request

from achilles.api.security_headers import SensitiveResponse
from achilles.auth.dependencies import CurrentUser
from achilles.auth.routes.common import record_audit
from achilles.auth.schemas import MeResponse, ProfilePatch, UserOut
from achilles.auth.services.audit import AuditAction
from achilles.db.dependencies import DbSession

router = APIRouter(prefix="/auth", tags=["auth"], dependencies=[SensitiveResponse])


@router.get("/me")
async def read_me(user: CurrentUser) -> MeResponse:
    """The current user plus the locale/date-format catalogues the editor renders."""
    return MeResponse(user=UserOut.model_validate(user))


@router.patch("/me")
async def update_me(
    body: ProfilePatch, user: CurrentUser, request: Request, session: DbSession
) -> UserOut:
    """Partial self-edit of name and region; absent fields stay, None clears to org default."""
    fields = body.model_dump(exclude_unset=True)
    for name, value in fields.items():
        setattr(user, name, value)
    await session.commit()
    await record_audit(
        request,
        action=AuditAction.USER_PROFILE_UPDATE,
        actor_id=user.id,
        target_type="user",
        target_id=str(user.id),
        meta={"fields": sorted(fields)},
    )
    return UserOut.model_validate(user)
