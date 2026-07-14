"""web_search preset: one schema, the provider behind an interface.

The model always sees web_search(query); which engine answers — Tavily,
Brave, Serper or Google CSE — is the admin's config choice, the key lives in
credential_enc. Consumers arrive with chat (stage 4) and agents (stage 6).
"""

from typing import Any

import httpx

from achilles.ai_foundation.constants import ToolAccess, ToolSource
from achilles.ai_foundation.tools.base import BaseTool, ProbeResult, ToolContext, ToolManifest
from achilles.ai_foundation.tools.registry import register_tool

_TIMEOUT = 15.0


def _request(
    provider: str, query: str, credential: str, config: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    """(url, request kwargs) per engine; one place knows the dialects."""
    match provider:
        case "tavily":
            return "https://api.tavily.com/search", {
                "json": {"api_key": credential, "query": query},
            }
        case "brave":
            return "https://api.search.brave.com/res/v1/web/search", {
                "params": {"q": query},
                "headers": {"X-Subscription-Token": credential},
            }
        case "serper":
            return "https://google.serper.dev/search", {
                "json": {"q": query},
                "headers": {"X-API-KEY": credential},
            }
        case "google_cse":
            return "https://www.googleapis.com/customsearch/v1", {
                "params": {"key": credential, "cx": config.get("cx", ""), "q": query},
            }
        case _:
            msg = f"unknown web_search provider {provider!r}"
            raise ValueError(msg)


@register_tool
class WebSearchTool(BaseTool):
    manifest = ToolManifest(
        name="web_search",
        access=ToolAccess.READ_ONLY,
        description="Search the public web for fresh external information.",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search query"}},
            "required": ["query"],
        },
        needs_credential=True,
        source=ToolSource.PRESET,
    )

    async def call(self, context: ToolContext, **arguments: object) -> object:
        query = str(arguments["query"])
        provider = str(context.config.get("provider", ""))
        if not provider or context.credential is None:
            msg = "web_search is not configured: provider and credential required"
            raise ValueError(msg)
        url, kwargs = _request(provider, query, context.credential, context.config)
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            method = "POST" if "json" in kwargs else "GET"
            response = await client.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()

    async def probe(self, context: ToolContext) -> ProbeResult:
        """A minimal search as the auth check — every engine bills per call anyway."""
        try:
            await self.call(context, query="ping")
        except (httpx.HTTPError, ValueError) as exc:
            return ProbeResult(ok=False, detail=str(exc))
        return ProbeResult(ok=True)
