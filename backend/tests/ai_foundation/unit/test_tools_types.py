"""Tool type registry and preset behaviour (unit)."""

import httpx
import pytest
import respx

from achilles.ai_foundation.constants import ToolAccess
from achilles.ai_foundation.tools.base import BaseTool, ProbeResult, ToolContext, ToolManifest
from achilles.ai_foundation.tools.fetch_url import (
    FetchUrlTool,
    _assert_public_url,  # pyright: ignore[reportPrivateUsage] — the SSRF guard is the test subject
)
from achilles.ai_foundation.tools.registry import (
    discover_tool_types,
    get_tool_type,
    register_tool,
    registered_tools,
)
from achilles.ai_foundation.tools.web_search import WebSearchTool

pytestmark = [pytest.mark.unit, pytest.mark.p1]


def test_presets_discovered():
    discover_tool_types()
    tools = registered_tools()
    assert {"web_search", "fetch_url"} <= set(tools)
    assert tools["web_search"].manifest.source == "preset"
    assert tools["web_search"].manifest.needs_credential
    assert not tools["fetch_url"].manifest.needs_credential


def test_duplicate_registration_dies():
    class Dupe(BaseTool):
        manifest = ToolManifest(name="web_search", access=ToolAccess.READ_ONLY, parameters={})

        async def call(self, context: ToolContext, **arguments: object) -> object:
            raise NotImplementedError

        async def probe(self, context: ToolContext) -> ProbeResult:
            raise NotImplementedError

    discover_tool_types()
    with pytest.raises(ValueError, match="duplicate"):
        register_tool(Dupe)


def test_write_access_type_dies_at_registration():
    class Writer(BaseTool):
        manifest = ToolManifest(name="rogue_writer", access=ToolAccess.WRITE, parameters={})

        async def call(self, context: ToolContext, **arguments: object) -> object:
            raise NotImplementedError

        async def probe(self, context: ToolContext) -> ProbeResult:
            raise NotImplementedError

    with pytest.raises(ValueError, match="read_only"):
        register_tool(Writer)
    assert get_tool_type("rogue_writer") is None


@respx.mock
async def test_web_search_probe_ok(respx_mock: respx.MockRouter):
    respx_mock.post("https://api.tavily.com/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    result = await WebSearchTool().probe(
        ToolContext(config={"provider": "tavily"}, credential="tv-key")
    )
    assert result.ok


@respx.mock
async def test_web_search_probe_bad_key(respx_mock: respx.MockRouter):
    respx_mock.post("https://api.tavily.com/search").mock(return_value=httpx.Response(401))
    result = await WebSearchTool().probe(
        ToolContext(config={"provider": "tavily"}, credential="bad")
    )
    assert not result.ok
    assert "401" in result.detail


async def test_web_search_unconfigured_probe_fails():
    result = await WebSearchTool().probe(ToolContext())
    assert not result.ok
    assert "not configured" in result.detail


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/file",
        "http://127.0.0.1/admin",
        "http://localhost:8000/",
        "not-a-url",
    ],
)
async def test_fetch_url_rejects_non_public(url: str):
    with pytest.raises(ValueError, match=r"URL|http|resolve|private"):
        await _assert_public_url(url)


@respx.mock
async def test_fetch_url_probe(respx_mock: respx.MockRouter):
    respx_mock.head("https://example.com").mock(return_value=httpx.Response(200))
    assert (await FetchUrlTool().probe(ToolContext())).ok
