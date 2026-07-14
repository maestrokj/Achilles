"""Wire contract of the Slack admin section (platform-settings.html#slack)."""

from datetime import datetime

from pydantic import BaseModel


class SlackSettingsOut(BaseModel):
    enabled: bool
    auto_link_by_email: bool
    team: str | None
    team_name: str | None
    bot_user_id: str | None
    bot_token_mask: str | None  # ••••xxxx, None = not set
    signing_secret_set: bool
    last_test_ok: bool | None
    last_test_at: datetime | None


class SlackSettingsPatch(BaseModel):
    """Partial by model_fields_set; an empty-string secret clears the column."""

    bot_token: str | None = None
    signing_secret: str | None = None
    enabled: bool | None = None
    auto_link_by_email: bool | None = None


class SlackTestOut(BaseModel):
    """Live-probe result: stamped into last_test_*, never a 5xx."""

    ok: bool
    team: str | None = None
    team_name: str | None = None
    bot_user_id: str | None = None
    error: str | None = None
