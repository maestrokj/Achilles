"""Auth & Security data model — data-model.html.

Conventions: BigInteger PK/FK · Text + CHECK instead of native ENUM ·
TIMESTAMPTZ · immutable rows carry created_at only.
"""

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from achilles.auth.constants import (
    AuditResult,
    AuthProvider,
    DateFormat,
    Locale,
    UserRole,
    UserStatus,
)
from achilles.db.base import Base, TimestampMixin, enum_check


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        sa.Index("uq_users_email_lower", sa.text("lower(email)"), unique=True),
        sa.CheckConstraint(f"role IN ({enum_check(UserRole)})", name="ck_users_role"),
        sa.CheckConstraint(f"status IN ({enum_check(UserStatus)})", name="ck_users_status"),
        sa.CheckConstraint(
            f"auth_provider IN ({enum_check(AuthProvider)})", name="ck_users_auth_provider"
        ),
        # Local accounts must have a hash; SSO accounts must not (v2-ready).
        sa.CheckConstraint(
            "(auth_provider = 'local') = (password_hash IS NOT NULL)",
            name="ck_users_local_needs_password",
        ),
        sa.CheckConstraint(f"locale IN ({enum_check(Locale)})", name="ck_users_locale"),
        sa.CheckConstraint(
            f"date_format IN ({enum_check(DateFormat)})", name="ck_users_date_format"
        ),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    email: Mapped[str] = mapped_column(sa.Text, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(sa.Text)
    full_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    role: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text(f"'{UserStatus.ACTIVE}'")
    )
    auth_provider: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text(f"'{AuthProvider.LOCAL}'")
    )
    must_change_password: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.false()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    timezone: Mapped[str | None] = mapped_column(sa.Text)  # IANA; NULL → org default
    locale: Mapped[str | None] = mapped_column(sa.Text)  # NULL → org default
    date_format: Mapped[str | None] = mapped_column(sa.Text)  # NULL → org default
    # The user's last explicit chat-model pick on a selectable surface — their
    # personal default, seeding new conversations. NULL → fall to the admin default.
    last_chat_model: Mapped[str | None] = mapped_column(sa.Text)
    # MFA is v2; columns are part of the designed schema (data-model.html, badge "MFA v2").
    mfa_enabled: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.false())
    mfa_secret: Mapped[str | None] = mapped_column(sa.Text)  # AES-256-GCM (v2)
    mfa_recovery: Mapped[list[str] | None] = mapped_column(JSONB)  # argon2id hashes (v2)


class RefreshToken(Base):
    """JWT-rotation state; rotation inserts a new row — immutable, created_at only."""

    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        sa.BigInteger,
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    family_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )  # UUIDv7, reuse-detection scope
    is_revoked: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.false())
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    absolute_expires_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False
    )
    # remember-me controls cookie lifetime only; rotation must reissue the same kind
    # of cookie, so the choice made at login is carried on the session row.
    remember_me: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.false())
    user_agent: Mapped[str | None] = mapped_column(sa.Text)
    ip: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


class InviteToken(Base):
    __tablename__ = "invite_tokens"
    __table_args__ = (
        sa.CheckConstraint(f"role IN ({enum_check(UserRole)})", name="ck_invite_tokens_role"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    email: Mapped[str] = mapped_column(sa.Text, nullable=False)
    role: Mapped[str] = mapped_column(sa.Text, nullable=False)
    token_hash: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    invited_by: Mapped[int] = mapped_column(
        sa.BigInteger,
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


class ResetToken(Base):
    __tablename__ = "reset_tokens"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        sa.BigInteger,
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


class LinkToken(Base):
    """Messenger-link one-time code; channel-neutral — the platform binds at confirm."""

    __tablename__ = "link_tokens"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        sa.BigInteger,
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    code_hash: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


class ApiKey(TimestampMixin, Base):
    """Machine access: key → user_id → role/ACL; mutable in place (revoke, last_used)."""

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        sa.BigInteger,
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    key_hash: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    prefix: Mapped[str] = mapped_column(sa.Text, nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(sa.Text)  # optional owner-facing label
    scope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True)
    )  # NULL → never expires
    last_used_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    is_revoked: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.false())
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class IdentityMapping(Base):
    """Login federation (SSO / slack / telegram) — distinct from KS content identity."""

    __tablename__ = "identity_mapping"
    __table_args__ = (
        sa.UniqueConstraint("source", "source_user_id", name="uq_identity_mapping_source_user"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        sa.BigInteger,
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source: Mapped[str] = mapped_column(sa.Text, nullable=False)
    source_user_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    source_email: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


class AuditLog(Base):
    """Append-only journal. actor_id has no FK on purpose: entries outlive their actor."""

    __tablename__ = "audit_log"
    __table_args__ = (
        sa.CheckConstraint(f"result IN ({enum_check(AuditResult)})", name="ck_audit_log_result"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(sa.BigInteger, index=True)  # NULL → system
    # Snapshot of the actor's email at write time — survives the actor's deletion.
    actor_email: Mapped[str | None] = mapped_column(sa.Text)
    action: Mapped[str] = mapped_column(sa.Text, nullable=False)
    target_type: Mapped[str | None] = mapped_column(sa.Text)
    target_id: Mapped[str | None] = mapped_column(sa.Text)
    result: Mapped[str] = mapped_column(sa.Text, nullable=False)
    ip: Mapped[str | None] = mapped_column(sa.Text)
    user_agent: Mapped[str | None] = mapped_column(sa.Text)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True
    )
