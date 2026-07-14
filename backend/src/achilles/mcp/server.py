"""FastMCP server factory: stateless streamable HTTP, one read-only tool.

Built per application (a StreamableHTTPSessionManager runs once per instance —
a module singleton would break the second create_app in tests/reload). The tool
handler runs inside the request's context (stateless mode), so the identity set
by the ASGI wrapper (asgi.py) arrives via a ContextVar — the SDK's own auth
stack is the OAuth 2.1 resource-server story, which is our v2.
"""

from contextvars import ContextVar
from dataclasses import dataclass

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from achilles.public_api import service
from achilles.public_api.constants import LIMIT_DEFAULT, LIMIT_MAX, QUERY_MAX_CHARS
from achilles.public_api.schemas import SearchOut


@dataclass(frozen=True, slots=True)
class McpIdentity:
    user_id: int
    scope: dict[str, object]
    session_factory: async_sessionmaker[AsyncSession]


current_identity: ContextVar[McpIdentity] = ContextVar("mcp_identity")


def build_server() -> FastMCP:
    server = FastMCP(
        "achilles",
        instructions=(
            "Search the company knowledge base. Returns findings with sources — "
            "synthesize the answer yourself."
        ),
        stateless_http=True,  # one read-only tool needs no MCP session store
        json_response=True,  # plain JSON responses — no SSE frames to buffer
        # Host validation is nginx's job; auth lives in the ASGI wrapper.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @server.tool()  # registration is the only reference
    async def search_knowledge(  # pyright: ignore[reportUnusedFunction]
        query: str, limit: int = LIMIT_DEFAULT
    ) -> SearchOut:
        """Search the company knowledge base; returns findings + sources, not an answer.

        Results are filtered by the key owner's access rights.
        """
        # Clamp instead of erroring — the caller is a model, not a UI.
        query = query[:QUERY_MAX_CHARS]
        limit = max(1, min(limit, LIMIT_MAX))
        identity = current_identity.get()
        async with identity.session_factory() as session:
            return await service.search_for_key(
                session, user_id=identity.user_id, scope=identity.scope, query=query, limit=limit
            )

    return server
