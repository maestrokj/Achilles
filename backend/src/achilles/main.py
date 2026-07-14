"""FastAPI application entry point."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from achilles.admin import maintenance as admin_maintenance
from achilles.ai_foundation.services import tokenizer
from achilles.ai_foundation.tools.registry import discover_tool_types
from achilles.api import API_PREFIX
from achilles.api.csrf import OriginCheckMiddleware
from achilles.api.health import ping_postgres, ping_redis
from achilles.api.problems import install_problem_handlers
from achilles.api.rate_limit import RATE_LIMIT_REMAINING_HEADER
from achilles.api.request_id import REQUEST_ID_HEADER, RequestIdMiddleware
from achilles.api.router import api_router, public_router
from achilles.api.security_headers import SecurityHeadersMiddleware
from achilles.config import Settings, settings
from achilles.db.connections import DbConnections, close_connections, create_connections
from achilles.harvester.connectors.registry import discover_connectors
from achilles.infra.redis import RedisPools, close_redis_pools, create_redis_pools
from achilles.mcp.asgi import register_mcp
from achilles.mcp.server import build_server

logger = logging.getLogger(__name__)

LOG_FORMAT = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

NOISY_LOGGERS = ("sqlalchemy.engine", "httpcore", "httpx", "uvicorn.access")


async def _warm_tokenizer(model_id: str | None) -> None:
    """Pre-resolve the assigned embedding model's tokenizer; failure is a shrug.

    Deliberately DB-free: this task gets cancel()ed on shutdown, and a second
    CancelledError pierces the shield around AsyncSession.close() — the close
    would outlive the task and still hold a pooled connection when dispose()
    runs, leaking the raw asyncpg connection. The model lookup therefore
    happens in the lifespan before this task is spawned.
    """
    if model_id is None:
        return
    try:
        await tokenizer.counter_for_model(model_id)
    except Exception:  # warmup must never take the app down with it
        logger.warning("tokenizer warmup failed — first use will retry", exc_info=True)


def create_app(app_settings: Settings) -> FastAPI:
    """Build the application; tests call this with their own settings."""
    mcp_server = build_server()  # per-app: its session manager runs once per instance

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[dict[str, DbConnections | RedisPools]]:
        app_level = getattr(logging, app_settings.log_level.upper(), logging.INFO)

        logging.basicConfig(format=LOG_FORMAT, datefmt=LOG_DATE_FORMAT, level=logging.WARNING)
        logging.getLogger("achilles").setLevel(app_level)
        for name in NOISY_LOGGERS:
            logging.getLogger(name).setLevel(max(app_level, logging.WARNING))

        # Tool/connector types self-register at import; unknown/broken types
        # die loudly here at startup, not on the first admin request.
        discover_tool_types()
        discover_connectors()

        db = create_connections(app_settings)
        redis = create_redis_pools(app_settings)

        await asyncio.gather(
            ping_postgres(db.pg_engine),
            ping_redis(redis.durable),
            ping_redis(redis.cache),
        )
        logger.info("All backing services connected")

        # The DB row survives a redis wipe — re-seed the maintenance mirror.
        async with db.pg_session_factory() as session:
            await admin_maintenance.sync_from_db(session, redis.durable)
            warm_model_id = await tokenizer.assigned_builtin_model_id(session)

        # Background, never blocks startup: without it the first chat turn of
        # a fresh process would stall on the tokenizer.json download before
        # its first SSE byte (the turn itself falls back to approx counting).
        warmup = asyncio.create_task(_warm_tokenizer(warm_model_id), name="tokenizer-warmup")

        # The streamable-HTTP session manager must run inside the app lifespan —
        # FastAPI does not execute a mounted app's own lifespan.
        async with mcp_server.session_manager.run():
            yield {"db": db, "redis": redis}

        # Await the unwind: a bare cancel() would let the loop close while the
        # task still holds the tokenizer resolve lock (module state) — the
        # next event loop in the process would then block on it forever.
        warmup.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await warmup
        await close_connections(db)
        await close_redis_pools(redis)

    app = FastAPI(title=app_settings.app_name, debug=app_settings.debug, lifespan=lifespan)
    app.state.settings = app_settings
    app.state.crypto_key = app_settings.derived_crypto_key()
    install_problem_handlers(app)
    # add_middleware prepends: listed innermost → outermost.
    app.add_middleware(OriginCheckMiddleware, allowed_origins=app_settings.cors_origins)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Authorization", REQUEST_ID_HEADER],
        expose_headers=["Retry-After", RATE_LIMIT_REMAINING_HEADER, REQUEST_ID_HEADER],
        max_age=3600,
    )
    app.add_middleware(RequestIdMiddleware)
    app.include_router(api_router, prefix=API_PREFIX)
    app.include_router(public_router)
    register_mcp(app, mcp_server)
    return app


app = create_app(settings)
