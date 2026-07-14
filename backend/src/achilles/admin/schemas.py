"""Platform settings contracts: admin editor (GET/PATCH) + the public branding read.

PATCH is partial: only fields present in the body apply (model_fields_set), so
None on a nullable field means "clear it". Cross-field rules that need the
merged row (weekly ⇒ weekday, alert ⇒ budget) live in service.apply_patch;
the DB CHECKs backstop them.
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from achilles.api.validators import validate_iana_timezone
from achilles.auth.constants import ACCESS_TOKEN_TTL_MAX
from achilles.knowledge_store.constants import (
    ACCENT_COLOR_PATTERN,
    WINDOW_TIME_PATTERN,
    CadenceFrequency,
    DateFormat,
    PlatformLocale,
)

# Upper bounds keep an oversized number at the 422 layer: without them the value
# clears Pydantic and overflows its column at flush, surfacing as a generic 500
# on plain user input. The ceilings mirror the destination column widths —
# INTEGER for the durations/intervals, BIGINT for the weekly token budgets.
_INT4_MAX = 2_147_483_647
_INT8_MAX = 9_223_372_036_854_775_807
_ACCESS_TTL_MAX = int(ACCESS_TOKEN_TTL_MAX.total_seconds())

type _Name = Annotated[str, Field(min_length=1, max_length=200)]
type _Positive = Annotated[int, Field(gt=0, le=_INT4_MAX)]
type _PositiveOrNone = Annotated[int, Field(gt=0, le=_INT8_MAX)] | None


class PlatformSettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    org_name: str
    org_logo_url: str | None
    org_description: str | None
    accent_color: str
    timezone: str
    locale: str
    date_format: str
    access_token_ttl: int
    refresh_token_ttl: int
    session_absolute_ttl: int
    maintenance_mode: bool
    mcp_enabled: bool
    ai_monthly_budget: Decimal | None
    ai_budget_alert_enabled: bool
    chat_weekly_token_budget: int | None
    agent_weekly_token_budget: int | None
    sync_interval_minutes: int
    reconcile_minute_of_week: int
    watchdog_silence_hours: int
    curation_frequency: str
    curation_weekday: int | None
    curation_time: str
    updated_at: datetime
    # Email seam (stage 9): the invites tab disables "Invite" while False.
    smtp_configured: bool = False
    # The backend is the source of the catalogues — the frontend renders, never lists.
    locale_choices: list[str] = Field(default_factory=lambda: [str(v) for v in PlatformLocale])
    date_format_choices: list[str] = Field(default_factory=lambda: [str(v) for v in DateFormat])


class PlatformSettingsPatch(BaseModel):
    org_name: _Name | None = None
    org_logo_url: str | None = None
    org_description: str | None = None
    accent_color: Annotated[str, Field(pattern=ACCENT_COLOR_PATTERN)] | None = None
    timezone: str | None = None
    locale: PlatformLocale | None = None
    date_format: DateFormat | None = None
    access_token_ttl: Annotated[int, Field(gt=0, le=_ACCESS_TTL_MAX)] | None = None
    refresh_token_ttl: _Positive | None = None
    session_absolute_ttl: _Positive | None = None
    maintenance_mode: bool | None = None
    mcp_enabled: bool | None = None
    ai_monthly_budget: Annotated[Decimal, Field(gt=0)] | None = None
    ai_budget_alert_enabled: bool | None = None
    chat_weekly_token_budget: _PositiveOrNone = None
    agent_weekly_token_budget: _PositiveOrNone = None
    sync_interval_minutes: _Positive | None = None
    reconcile_minute_of_week: Annotated[int, Field(ge=0, le=10079)] | None = None
    watchdog_silence_hours: _Positive | None = None
    curation_frequency: CadenceFrequency | None = None
    curation_weekday: Annotated[int, Field(ge=0, le=6)] | None = None
    curation_time: Annotated[str, Field(pattern=WINDOW_TIME_PATTERN)] | None = None

    _known_iana_zone = field_validator("timezone")(validate_iana_timezone)


class BrandingOut(BaseModel):
    """The anonymous slice: what the login screen and the shell chrome need."""

    model_config = ConfigDict(from_attributes=True)

    org_name: str
    org_logo_url: str | None
    accent_color: str
    timezone: str
    locale: str
    date_format: str
