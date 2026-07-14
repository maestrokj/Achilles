import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from logging.config import fileConfig

from alembic import context
from alembic.operations.ops import MigrationScript
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

import achilles.agent_engine.models
import achilles.ai_foundation.models
import achilles.auth.models
import achilles.email.models
import achilles.harvester.models
import achilles.knowledge_store.models
import achilles.mattermost.models
import achilles.notifications.models
import achilles.query_engine.models
import achilles.slack.models
import achilles.telegram.models  # noqa: F401
from achilles.config import settings
from achilles.db.base import Base

config = context.config
# Tests pass their own container URL via Config; fall back to app settings otherwise.
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _generate_revision_id(
    _context: MigrationContext,
    _revision: str | Sequence[str] | None,
    directives: list[MigrationScript],
) -> None:
    for directive in directives:
        directive.rev_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")


_CONTEXT_OPTS = {
    "target_metadata": target_metadata,
    "compare_type": True,
    "compare_server_default": True,
    "process_revision_directives": _generate_revision_id,
}


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, literal_binds=True, transaction_per_migration=True, **_CONTEXT_OPTS)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, transaction_per_migration=True, **_CONTEXT_OPTS)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
