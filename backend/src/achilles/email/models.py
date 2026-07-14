"""smtp_settings singleton — the transport core of the Email module.

Design: email/_workzone/data-model.html. Write-only encrypted password, one
`is_enabled` master switch, availability derived on the model — the single
truth for Email itself and its consumers (invites, password reset).
"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from achilles.db.base import Base, TimestampMixin
from achilles.email.constants import SmtpSecurity


class SmtpSettings(TimestampMixin, Base):
    """Singleton (CHECK id = 1): the migration seeds, the app reads/updates."""

    __tablename__ = "smtp_settings"
    __table_args__ = (
        sa.CheckConstraint("id = 1", name="ck_smtp_settings_singleton"),
        sa.CheckConstraint(
            "security IN ('none', 'starttls', 'ssl_tls')", name="ck_smtp_settings_security"
        ),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    host: Mapped[str | None] = mapped_column(sa.Text)
    port: Mapped[int | None] = mapped_column(sa.Integer)
    security: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text(f"'{SmtpSecurity.STARTTLS.value}'")
    )
    username: Mapped[str | None] = mapped_column(sa.Text)
    password_enc: Mapped[str | None] = mapped_column(sa.Text)  # AES-256-GCM, write-only
    from_address: Mapped[str | None] = mapped_column(sa.Text)  # RFC 5322: "Name <a@b>"
    is_enabled: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("false")
    )
    last_test_ok: Mapped[bool | None] = mapped_column(sa.Boolean)
    last_test_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    @property
    def is_available(self) -> bool:
        """The master switch is on AND everything a send needs is present.

        last_test_ok is advice, not a gate — delivery insurance is queue retries.
        """
        return bool(self.is_enabled and self.host and self.port and self.from_address)
