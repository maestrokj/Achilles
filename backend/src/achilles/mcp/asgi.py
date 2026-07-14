"""ASGI endpoint on the exact /mcp path: auth + kill-switch in front of the SDK app.

Registered as a Starlette Route with an ASGI-class endpoint — a Mount would
307-redirect the bare /mcp that every client posts to. ApiError raised here
renders as problem+json through the app-level handlers.
"""

from datetime import UTC, datetime

from fastapi import FastAPI, Response
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from achilles.api.problems import ApiError
from achilles.auth.dependencies import extract_api_key, resolve_key_request
from achilles.db.connections import DbConnections
from achilles.knowledge_store.services.maintenance import ensure_not_maintenance
from achilles.knowledge_store.services.platform import get_platform_settings
from achilles.mcp.constants import CODE_MCP_DISABLED, MCP_PATH
from achilles.mcp.server import McpIdentity, current_identity


class McpEndpoint:
    def __init__(self, mcp_app: ASGIApp) -> None:
        self._mcp_app = mcp_app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        request = Request(scope)
        token = extract_api_key(request)

        db: DbConnections = request.state.db
        async with db.pg_session_factory() as session:
            platform = await get_platform_settings(session)
            if not platform.mcp_enabled:
                # The door is locked for everyone at once; the keys stay alive.
                raise ApiError(
                    403,
                    CODE_MCP_DISABLED,
                    "Forbidden",
                    "MCP access is disabled by the administrator",
                )
            # Throwaway Response: the rate-limit header has no JSON-RPC seat.
            key, user = await resolve_key_request(
                request, Response(), session, token, datetime.now(UTC), allow_mutating=True
            )
            # Same 503 the Public API and retrieval routes answer while a restore
            # overwrites the store — search over a half-restored DB is meaningless.
            await ensure_not_maintenance(request)
            identity = McpIdentity(
                user_id=user.id, scope=key.scope or {}, session_factory=db.pg_session_factory
            )

        # Stateless mode runs the tool handler within this request's context,
        # so a ContextVar carries the identity into it.
        current_identity.set(identity)
        await self._mcp_app(scope, receive, send)


def register_mcp(app: FastAPI, server: FastMCP) -> None:
    """Build the streamable app and hang it on /mcp.

    Must precede lifespan start — session_manager exists only after
    streamable_http_app().
    """
    endpoint = McpEndpoint(server.streamable_http_app())
    app.router.routes.append(Route(MCP_PATH, endpoint=endpoint, methods=["POST"]))
