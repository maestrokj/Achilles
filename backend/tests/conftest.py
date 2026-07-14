"""Root test scaffold: env bootstrap, session containers, app/client fixtures.

Settings() is instantiated at import time in achilles.config, so the env must be
complete BEFORE the first `achilles` import. Placeholders below are overridden by
real container URLs in `test_settings`; `pytest -m unit` never touches Docker
(container fixtures are lazy).
"""

import os

_TEST_ENV = {
    "SECRET_KEY": "test-secret-key-not-for-prod-0123456789",
    "DATABASE_URL": "postgresql+asyncpg://unset:unset@127.0.0.1:1/unset",
    "REDIS_DURABLE_URL": "redis://127.0.0.1:1/0",
    "REDIS_CACHE_URL": "redis://127.0.0.1:1/1",
    "CORS_ORIGINS": '["https://app.test"]',
}
for _key, _value in _TEST_ENV.items():
    os.environ.setdefault(_key, _value)

from collections.abc import AsyncGenerator, Awaitable, Callable, Generator  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config as AlembicConfig  # noqa: E402
from asgi_lifespan import LifespanManager  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from redis.asyncio import Redis  # noqa: E402
from testcontainers.postgres import PostgresContainer  # noqa: E402
from testcontainers.redis import RedisContainer  # noqa: E402

from achilles.config import Settings  # noqa: E402
from achilles.main import create_app  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parents[1]

TEST_ORIGIN = "https://app.test"

# Lifespan opens fresh DB/Redis connections through Docker's port proxy; under
# full xdist load a cold startup occasionally overruns the 5s library default.
LIFESPAN_TIMEOUT = 30.0


@pytest.fixture(scope="session")
def pg_url() -> Generator[str]:
    with PostgresContainer("pgvector/pgvector:pg17", driver="asyncpg") as pg:
        # Same literal-IPv4 story as redis_urls below.
        yield pg.get_connection_url().replace("localhost", "127.0.0.1")


@pytest.fixture(scope="session")
def migrated_pg_url(pg_url: str) -> str:
    # No ini file on purpose: env.py's fileConfig would disable already-created loggers.
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", pg_url)
    command.upgrade(cfg, "head")
    return pg_url


@pytest.fixture(scope="session")
def redis_urls() -> Generator[tuple[str, str]]:
    with RedisContainer("redis:7-alpine") as rc:
        # Literal IPv4: 'localhost' makes every connect race ::1 against
        # 127.0.0.1 (Happy Eyeballs) through Docker's port proxy, which
        # under xdist load intermittently refuses the IPv6 leg — the whole
        # connect then dies with 'Multiple exceptions: [Errno 0] … [Errno 60]'.
        host = rc.get_container_host_ip().replace("localhost", "127.0.0.1")
        port = rc.get_exposed_port(6379)
        # One container, two logical instances: durable on db0, cache on db1.
        yield (f"redis://{host}:{port}/0", f"redis://{host}:{port}/1")


@pytest.fixture(scope="session")
def test_settings(migrated_pg_url: str, redis_urls: tuple[str, str]) -> Settings:
    return Settings(
        secret_key="test-secret-key-not-for-prod-0123456789",
        database_url=migrated_pg_url,
        redis_durable_url=redis_urls[0],
        redis_cache_url=redis_urls[1],
        cors_origins=[TEST_ORIGIN],
        log_level="WARNING",
    )


type FlushRedis = Callable[[], Awaitable[None]]


@pytest.fixture(scope="session")
async def flush_redis(test_settings: Settings) -> AsyncGenerator[FlushRedis]:
    # Persistent per-worker clients: a fresh connect per test multiplied by the
    # worker count overwhelms Docker's port proxy (same ETIMEDOUT story as the
    # pooled db_engine).
    clients = [
        Redis.from_url(url)
        for url in (test_settings.redis_durable_url, test_settings.redis_cache_url)
    ]

    async def _flush() -> None:
        for client in clients:
            await client.flushdb()  # type: ignore[misc]

    yield _flush
    for client in clients:
        await client.aclose()


@pytest.fixture
def app(test_settings: Settings) -> FastAPI:
    return create_app(test_settings)


@pytest.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient]:
    # https base_url so Secure/__Secure- cookies round-trip through the test client.
    async with LifespanManager(
        app, startup_timeout=LIFESPAN_TIMEOUT, shutdown_timeout=LIFESPAN_TIMEOUT
    ) as manager:
        transport = ASGITransport(app=manager.app)
        async with AsyncClient(transport=transport, base_url="https://testserver") as c:
            yield c


class ClientFactory:
    """Build a client against an app with per-test Settings overrides."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @asynccontextmanager
    async def __call__(self, **overrides: object) -> AsyncGenerator[AsyncClient]:
        app = create_app(self._settings.model_copy(update=dict(overrides)))
        async with LifespanManager(
            app, startup_timeout=LIFESPAN_TIMEOUT, shutdown_timeout=LIFESPAN_TIMEOUT
        ) as manager:
            transport = ASGITransport(app=manager.app)
            async with AsyncClient(transport=transport, base_url="https://testserver") as c:
                yield c


@pytest.fixture
def client_factory(test_settings: Settings) -> ClientFactory:
    return ClientFactory(test_settings)
