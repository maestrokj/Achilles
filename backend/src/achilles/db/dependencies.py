"""FastAPI dependencies for database access."""

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.db.connections import DbConnections


def get_db(request: Request) -> DbConnections:
    return request.state.db


async def get_session(request: Request) -> AsyncGenerator[AsyncSession]:
    db: DbConnections = request.state.db
    async with db.pg_session_factory() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(get_session)]
