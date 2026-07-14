"""Request/response models for the auth family."""

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from achilles.api.serialization import UtcDateTime
from achilles.api.validators import validate_iana_timezone
from achilles.auth.constants import DateFormat, Locale


class SetupRequest(BaseModel):
    email: EmailStr
    full_name: str
    password: str


class SetupStatus(BaseModel):
    """Anonymous first-run probe: is the platform still awaiting its Owner?"""

    needs_setup: bool


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    remember_me: bool = False


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str
    role: str
    status: str
    must_change_password: bool
    timezone: str | None
    locale: str | None
    date_format: str | None
    last_login_at: UtcDateTime | None
    created_at: UtcDateTime


class SessionResponse(BaseModel):
    """Access token travels in the body (JS memory); refresh — in the httpOnly cookie."""

    access_token: str
    token_type: str = "bearer"  # noqa: S105 — OAuth token type label, not a secret
    must_change_password: bool
    user: UserOut


class MeResponse(BaseModel):
    """The self-service profile read: the user plus the catalogues the editor needs.

    The backend owns the value catalogues — the frontend renders, never lists.
    """

    user: UserOut
    locale_choices: list[str] = Field(default_factory=lambda: [str(v) for v in Locale])
    date_format_choices: list[str] = Field(default_factory=lambda: [str(v) for v in DateFormat])


class ProfilePatch(BaseModel):
    """Partial self-edit: only fields present in the body apply (model_fields_set).

    ``None`` on a nullable region field means "fall back to the org default";
    ``full_name`` cannot be cleared, so it is non-nullable when present.
    """

    full_name: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    timezone: str | None = None
    locale: Locale | None = None
    date_format: DateFormat | None = None

    _known_iana_zone = field_validator("timezone")(validate_iana_timezone)

    @field_validator("full_name")
    @classmethod
    def _name_not_cleared(cls, value: str | None) -> str | None:
        """Absent leaves the name as is; an explicit null is a 422, not a NOT NULL 500."""
        if value is None:
            msg = "full_name cannot be null"
            raise ValueError(msg)
        return value


class SessionInfo(BaseModel):
    """One active device session (a refresh-token family), for the sessions screen."""

    id: UUID
    user_agent: str | None
    ip: str | None
    created_at: UtcDateTime
    is_current: bool


class SessionListResponse(BaseModel):
    items: list[SessionInfo]
