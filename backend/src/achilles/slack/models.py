"""slack_settings singleton — the shared core of the Slack surface (slack/index.html#data).

Mirror of the Email SMTP pattern: write-only encrypted secrets, one `enabled`
master switch, derived availability instead of a stored flag.
"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from achilles.db.base import Base, TimestampMixin


class SlackSettings(TimestampMixin, Base):
    """Singleton (CHECK id = 1): the migration seeds, the app reads/updates."""

    __tablename__ = "slack_settings"
    __table_args__ = (sa.CheckConstraint("id = 1", name="ck_slack_settings_singleton"),)

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    team: Mapped[str | None] = mapped_column(sa.Text)  # T… workspace id, from auth.test
    team_name: Mapped[str | None] = mapped_column(sa.Text)
    bot_token_enc: Mapped[str | None] = mapped_column(sa.Text)  # xoxb-…, AES-256-GCM
    signing_secret_enc: Mapped[str | None] = mapped_column(sa.Text)  # inbound signature check
    bot_user_id: Mapped[str | None] = mapped_column(sa.Text)  # U…, drops the bot's own posts
    enabled: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("false")
    )
    # On by default: a DM from a known work email links to that account. Turned
    # off where members can edit their own work email (self-service spoofing).
    auto_link_by_email: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("true")
    )
    last_test_ok: Mapped[bool | None] = mapped_column(sa.Boolean)
    last_test_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    @property
    def is_available(self) -> bool:
        """The master switch is on AND everything the webhook needs is present."""
        return bool(self.enabled and self.team and self.bot_token_enc and self.signing_secret_enc)
