"""Wire contract of the Mattermost admin section (platform-settings.html#mattermost).

Deliberately no public-address guard on base_url: Achilles is self-hosted and
the Mattermost server legitimately lives on a private LAN. Telegram's
webhook_base_is_public protects the inverse direction — a cloud that must reach
*us* — which does not apply to an outbound listener.
"""

from datetime import datetime
from urllib.parse import urlparse

from pydantic import BaseModel, field_validator


class MattermostSettingsOut(BaseModel):
    enabled: bool
    base_url: str | None
    bot_username: str | None
    bot_token_mask: str | None  # ••••xxxx, None = not set
    listener_connected: bool | None  # live WebSocket health; None = unknown / not running
    last_test_ok: bool | None
    last_test_at: datetime | None


class MattermostSettingsPatch(BaseModel):
    """Partial by model_fields_set; an empty-string secret clears the column."""

    base_url: str | None = None
    bot_token: str | None = None
    enabled: bool | None = None

    @field_validator("base_url")
    @classmethod
    def _sane_http_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().rstrip("/")
        if not value:
            return None  # clearing the field, like an empty-string secret
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            msg = "base_url must be an http(s) URL with a host"
            raise ValueError(msg)
        return value


class MattermostTestOut(BaseModel):
    """Live-probe result: stamped into last_test_*, never a 5xx."""

    ok: bool
    bot_username: str | None = None
    error: str | None = None
