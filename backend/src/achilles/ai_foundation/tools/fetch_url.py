"""fetch_url preset: read a public web page for the model (tool-catalog.html).

Keyless and read-only. The guard here is SSRF: only http/https, no requests
into private or link-local ranges, a hard size cap on the body — the tool
reaches the open web, not the deployment's own network. The check runs inside
the transport, so it re-validates every redirect hop, not just the first URL.
"""

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

import httpx

from achilles.ai_foundation.constants import ToolAccess, ToolSource
from achilles.ai_foundation.tools.base import BaseTool, ProbeResult, ToolContext, ToolManifest
from achilles.ai_foundation.tools.registry import register_tool

_TIMEOUT = 15.0
_MAX_BODY_BYTES = 2 * 1024 * 1024
_ALLOWED_SCHEMES = ("http", "https")


async def _assert_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES or not parsed.hostname:
        msg = "only absolute http(s) URLs are allowed"
        raise ValueError(msg)
    try:
        # Async resolve: a slow DNS answer must stall this call, not the loop.
        infos = await asyncio.get_running_loop().getaddrinfo(parsed.hostname, None)
    except socket.gaierror as exc:
        msg = f"cannot resolve host {parsed.hostname!r}"
        raise ValueError(msg) from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            msg = "URL resolves into a private network"
            raise ValueError(msg)


class _PublicOnlyTransport(httpx.AsyncHTTPTransport):
    """Re-run the SSRF guard for every request the client makes.

    Covers the initial fetch and each redirect hop, so a 302 into a private
    host cannot slip past a one-time pre-flight check.
    """

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await _assert_public_url(str(request.url))
        return await super().handle_async_request(request)


@register_tool
class FetchUrlTool(BaseTool):
    manifest = ToolManifest(
        name="fetch_url",
        access=ToolAccess.READ_ONLY,
        description="Fetch a public web page by URL and return its content.",
        parameters={
            "type": "object",
            "properties": {"url": {"type": "string", "description": "Absolute http(s) URL"}},
            "required": ["url"],
        },
        source=ToolSource.PRESET,
    )

    async def call(self, context: ToolContext, **arguments: object) -> object:
        del context  # keyless; no config in v1
        url = str(arguments["url"])
        async with (
            httpx.AsyncClient(
                timeout=_TIMEOUT, follow_redirects=True, transport=_PublicOnlyTransport()
            ) as client,
            client.stream("GET", url) as response,
        ):
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > _MAX_BODY_BYTES:
                    msg = f"response exceeds {_MAX_BODY_BYTES} bytes"
                    raise ValueError(msg)
        return {
            "url": str(response.url),
            "content_type": response.headers.get("content-type", ""),
            "body": body.decode("utf-8", errors="replace"),
        }

    async def probe(self, context: ToolContext) -> ProbeResult:
        del context  # nothing to authenticate; reaching the open web is the check
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                response = await client.head("https://example.com")
                response.raise_for_status()
        except httpx.HTTPError as exc:
            return ProbeResult(ok=False, detail=str(exc))
        return ProbeResult(ok=True)
