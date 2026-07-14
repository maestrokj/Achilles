"""mattermost_settings singleton — shared core of the surface (mattermost/index.html#data).

Mirror of the Email SMTP pattern: a write-only encrypted secret, one `enabled`
master switch, derived availability instead of a stored flag. Differs from the
webhook twins in what the transport dictates: the server address is a setting
(any API-v4-compatible installation), and there is no webhook secret at all —
the listener dials out, nothing dials in. `bot_user_id` is stamped only by a
successful live probe, so its presence is the honest "the token really answered"
signal that webhook_secret_enc plays for Telegram.
"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from achilles.db.base import Base, TimestampMixin


class MattermostSettings(TimestampMixin, Base):
    """Singleton (CHECK id = 1): the migration seeds, the app reads/updates."""

    __tablename__ = "mattermost_settings"
    __table_args__ = (sa.CheckConstraint("id = 1", name="ck_mattermost_settings_singleton"),)

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    base_url: Mapped[str | None] = mapped_column(sa.Text)  # https://mattermost.example.com
    bot_token_enc: Mapped[str | None] = mapped_column(sa.Text)  # bot access token, AES-256-GCM
    bot_user_id: Mapped[str | None] = mapped_column(sa.Text)  # from /users/me; filters own posts
    bot_username: Mapped[str | None] = mapped_column(sa.Text)  # @handle for the UI, from /users/me
    enabled: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("false")
    )
    last_test_ok: Mapped[bool | None] = mapped_column(sa.Boolean)
    last_test_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    @property
    def is_available(self) -> bool:
        """The master switch is on AND everything the listener needs is present.

        `bot_user_id` is filled only after a successful probe, so its presence
        means the token has actually answered — and the listener can tell the
        bot's own posts apart from the person's.
        """
        return bool(self.enabled and self.base_url and self.bot_token_enc and self.bot_user_id)
