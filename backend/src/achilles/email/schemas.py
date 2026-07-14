"""Wire contract of the SMTP admin section (platform-settings.html#smtp)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SmtpSettingsOut(BaseModel):
    is_enabled: bool
    host: str | None
    port: int | None
    security: str
    username: str | None
    password_mask: str | None  # ••••xxxx, None = not set
    from_address: str | None
    is_available: bool
    last_test_ok: bool | None
    last_test_at: datetime | None


class SmtpSettingsPatch(BaseModel):
    """Partial by model_fields_set; an empty-string password clears the column."""

    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    security: Literal["none", "starttls", "ssl_tls"] | None = None
    username: str | None = None
    password: str | None = None
    from_address: str | None = None
    is_enabled: bool | None = None


class SmtpTestOut(BaseModel):
    """Inline-probe result: stamped into last_test_*, never a 5xx."""

    ok: bool
    error: str | None = None
