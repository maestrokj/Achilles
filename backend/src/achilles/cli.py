"""Operator CLI: `achilles init-owner` · `achilles reset-password`.

Shares the bootstrap path (advisory lock) with the Setup Wizard and the
temp-password fallback with the admin reset — authentication.html#setup-wizard,
#admin-reset. Runs next to the app (same env), talks to the DB directly.
"""

import argparse
import asyncio
import sys

import sqlalchemy as sa

from achilles.api.problems import ApiError
from achilles.auth.constants import AuditResult, UserStatus
from achilles.auth.models import User
from achilles.auth.services import audit, bootstrap, users_admin
from achilles.auth.services.audit import AuditAction
from achilles.config import Settings, settings
from achilles.db.connections import close_connections, create_connections


async def init_owner(app_settings: Settings, *, email: str, password: str, full_name: str) -> int:
    db = create_connections(app_settings)
    try:
        async with db.pg_session_factory() as session:
            try:
                owner = await bootstrap.create_owner(
                    session, email=email, full_name=full_name, password=password
                )
                await session.commit()
            except ApiError as exc:
                print(f"error: {exc.detail or exc.title}", file=sys.stderr)
                return 1
        await audit.record(
            db.pg_session_factory,
            action=AuditAction.SETUP,
            result=AuditResult.SUCCESS,
            actor_id=owner.id,
            target_type="user",
            target_id=str(owner.id),
            meta={"via": "cli"},
        )
        print(f"Owner created: {owner.email} (id={owner.id})")
        return 0
    finally:
        await close_connections(db)


async def reset_password(app_settings: Settings, *, email: str) -> int:
    """CSPRNG temp password, shown once; all sessions killed; change forced at login."""
    db = create_connections(app_settings)
    try:
        session_factory = db.pg_session_factory
        async with session_factory() as session:
            user = await session.scalar(
                sa.select(User).where(sa.func.lower(User.email) == email.lower())
            )
            if user is None or user.status != UserStatus.ACTIVE.value:
                print("error: no active user with this email", file=sys.stderr)
                return 1
            temp_password = await users_admin.admin_reset_password(session, user)
            await session.commit()
            user_id = user.id
        await audit.record(
            session_factory,
            action=AuditAction.PASSWORD_RESET,
            result=AuditResult.SUCCESS,
            target_type="user",
            target_id=str(user_id),
            meta={"via": "cli"},
        )
        print(f"Temporary password (shown once): {temp_password}")
        return 0
    finally:
        await close_connections(db)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="achilles", description="Achilles operator CLI")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init-owner", help="create the first Owner (only at 0 users)")
    init.add_argument("--email", required=True)
    init.add_argument("--password", required=True)
    init.add_argument("--name", default="Owner", help="full name (default: Owner)")

    reset = commands.add_parser("reset-password", help="issue a one-time temporary password")
    reset.add_argument("--email", required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "init-owner":
        return asyncio.run(
            init_owner(settings, email=args.email, password=args.password, full_name=args.name)
        )
    if args.command == "reset-password":
        return asyncio.run(reset_password(settings, email=args.email))
    return 2


if __name__ == "__main__":
    sys.exit(main())
