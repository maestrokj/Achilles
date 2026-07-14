"""Wire contract of the Telegram admin section (platform-settings.html#telegram)."""

from datetime import datetime

from pydantic import BaseModel


class TelegramSettingsOut(BaseModel):
    enabled: bool
    bot_username: str | None
    bot_token_mask: str | None  # ••••xxxx, None = not set
    webhook_secret_set: bool  # Achilles owns it; the admin only sees whether it exists
    last_test_ok: bool | None
    last_test_at: datetime | None


class TelegramSettingsPatch(BaseModel):
    """Partial by model_fields_set; an empty-string secret clears the column."""

    bot_token: str | None = None
    enabled: bool | None = None


class TelegramTestOut(BaseModel):
    """Live-probe result: stamped into last_test_*, never a 5xx."""

    ok: bool
    bot_username: str | None = None
    error: str | None = None
