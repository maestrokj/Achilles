"""Database connection lifecycle management."""

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from achilles.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DbConnections:
    pg_engine: AsyncEngine
    pg_session_factory: async_sessionmaker[AsyncSession]


def create_connections(settings: Settings) -> DbConnections:
    pg_engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=settings.pg_pool_size,
        max_overflow=settings.pg_pool_max_overflow,
        pool_recycle=settings.pg_pool_recycle,
    )
    return DbConnections(
        pg_engine=pg_engine,
        pg_session_factory=async_sessionmaker(pg_engine, expire_on_commit=False),
    )


async def close_connections(db: DbConnections) -> None:
    try:
        await db.pg_engine.dispose()
    except Exception:
        logger.warning("Failed to close PostgreSQL cleanly", exc_info=True)
