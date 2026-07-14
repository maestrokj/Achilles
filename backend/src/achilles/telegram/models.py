"""telegram_settings singleton — the shared core of the Telegram surface (telegram/index.html#data).

Mirror of the Email SMTP pattern: write-only encrypted secrets, one `enabled`
master switch, derived availability instead of a stored flag. Differs from
slack_settings in one thing — Achilles generates the webhook secret itself at
setWebhook, so there is no admin-entered signing secret and no email → no
auto-link column.
"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from achilles.db.base import Base, TimestampMixin


class TelegramSettings(TimestampMixin, Base):
    """Singleton (CHECK id = 1): the migration seeds, the app reads/updates."""

    __tablename__ = "telegram_settings"
    __table_args__ = (sa.CheckConstraint("id = 1", name="ck_telegram_settings_singleton"),)

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    bot_token_enc: Mapped[str | None] = mapped_column(sa.Text)  # 12345:ABC…, AES-256-GCM
    webhook_secret_enc: Mapped[str | None] = mapped_column(sa.Text)  # generated at setWebhook
    bot_username: Mapped[str | None] = mapped_column(sa.Text)  # @handle for the UI, from getMe
    enabled: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("false")
    )
    last_test_ok: Mapped[bool | None] = mapped_column(sa.Boolean)
    last_test_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    @property
    def is_available(self) -> bool:
        """The master switch is on AND everything the webhook needs is present.

        The webhook secret is filled only after a successful setWebhook, so its
        presence is the honest signal that Telegram is actually delivering.
        """
        return bool(self.enabled and self.bot_token_enc and self.webhook_secret_enc)
