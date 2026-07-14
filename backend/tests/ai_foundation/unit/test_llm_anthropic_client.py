"""AnthropicChatClient: Messages SSE → common events (unit)."""

import json

import pytest
import respx
from httpx import Response

from achilles.ai_foundation.llm import AnthropicChatClient, ChatMessage, ToolCall, ToolSpec
from achilles.ai_foundation.llm.types import (
    ProviderUnavailableError,
    StreamEnd,
    TextDelta,
    ToolCallsReady,
    Usage,
)
from tests.ai_foundation.unit.llm_wire import (
    anthropic_message_start,
    anthropic_sse,
    anthropic_tail,
    anthropic_text_block,
    anthropic_text_body,
    anthropic_tool_block,
    collect,
)

pytestmark = [pytest.mark.unit, pytest.mark.p1]

BASE = "https://claude.test"
URL = f"{BASE}/v1/messages"

SEARCH_SPEC = ToolSpec(
    name="web_search",
    description="Search the web.",
    parameters={"type": "object", "properties": {"query": {"type": "string"}}},
)


def make_client() -> AnthropicChatClient:
    return AnthropicChatClient(api_key="test-key", base_url=BASE)


def mock_messages(router: respx.MockRouter, body: bytes) -> respx.Route:
    return router.post(URL).mock(
        return_value=Response(200, content=body, headers={"content-type": "text/event-stream"})
    )


async def test_plain_text_stream(hibp_clean: respx.MockRouter):
    route = mock_messages(
        hibp_clean, anthropic_text_body("Hel", "lo", input_tokens=9, output_tokens=3)
    )
    events = await collect(
        make_client().stream(
            model="claude-test",
            system="Be terse.",
            messages=[ChatMessage(role="user", content="hi")],
            max_tokens=128,
        )
    )
    assert events == [
        TextDelta("Hel"),
        TextDelta("lo"),
        Usage(input_tokens=9, output_tokens=3),
        StreamEnd(),
    ]
    body = json.loads(route.calls.last.request.read())
    assert body["system"] == "Be terse."
    assert body["max_tokens"] == 128
    assert body["stream"] is True
    assert "tools" not in body


async def test_tools_are_wired_with_input_schema(hibp_clean: respx.MockRouter):
    route = mock_messages(hibp_clean, anthropic_text_body("ok"))
    await collect(
        make_client().stream(
            model="claude-test",
            system="s",
            messages=[ChatMessage(role="user", content="hi")],
            tools=[SEARCH_SPEC],
            max_tokens=64,
        )
    )
    body = json.loads(route.calls.last.request.read())
    assert body["tools"] == [
        {
            "name": "web_search",
            "description": "Search the web.",
            "input_schema": SEARCH_SPEC.parameters,
        }
    ]
    assert "tool_choice" not in body  # "auto" is the upstream default — not spelled out


async def test_tool_choice_none_forbids_calls_but_keeps_catalog(hibp_clean: respx.MockRouter):
    # Round 2 of the harness: tool_use/tool_result history REQUIRES the tools
    # param on this dialect — "none" is what makes the final answer mandatory.
    route = mock_messages(hibp_clean, anthropic_text_body("ok"))
    await collect(
        make_client().stream(
            model="claude-test",
            system="s",
            messages=[ChatMessage(role="user", content="hi")],
            tools=[SEARCH_SPEC],
            tool_choice="none",
            max_tokens=64,
        )
    )
    body = json.loads(route.calls.last.request.read())
    assert body["tool_choice"] == {"type": "none"}
    assert [t["name"] for t in body["tools"]] == ["web_search"]


async def test_tool_use_block_with_fragmented_json(hibp_clean: respx.MockRouter):
    body = anthropic_sse(
        anthropic_message_start(input_tokens=11),
        *anthropic_text_block(0, "Let me check."),
        *anthropic_tool_block(
            1, id="toolu_1", name="web_search", fragments=('{"query": ', '"cats"}')
        ),
        *anthropic_tail(output_tokens=25, stop_reason="tool_use"),
    )
    mock_messages(hibp_clean, body)
    events = await collect(
        make_client().stream(
            model="claude-test",
            system="s",
            messages=[ChatMessage(role="user", content="hi")],
            tools=[SEARCH_SPEC],
            max_tokens=64,
        )
    )
    assert events == [
        TextDelta("Let me check."),
        ToolCallsReady((ToolCall(id="toolu_1", name="web_search", arguments={"query": "cats"}),)),
        Usage(input_tokens=11, output_tokens=25),
        StreamEnd(),
    ]


async def test_two_tool_use_blocks(hibp_clean: respx.MockRouter):
    body = anthropic_sse(
        anthropic_message_start(),
        *anthropic_tool_block(0, id="toolu_a", name="web_search", fragments=('{"query": "a"}',)),
        *anthropic_tool_block(
            1, id="toolu_b", name="fetch_url", fragments=('{"url": "https://b"}',)
        ),
        *anthropic_tail(stop_reason="tool_use"),
    )
    mock_messages(hibp_clean, body)
    events = await collect(
        make_client().stream(
            model="claude-test",
            system="s",
            messages=[ChatMessage(role="user", content="hi")],
            tools=[SEARCH_SPEC],
            max_tokens=64,
        )
    )
    ready = events[0]
    assert isinstance(ready, ToolCallsReady)
    assert ready.calls == (
        ToolCall(id="toolu_a", name="web_search", arguments={"query": "a"}),
        ToolCall(id="toolu_b", name="fetch_url", arguments={"url": "https://b"}),
    )


async def test_history_folds_tool_results_into_one_user_message(hibp_clean: respx.MockRouter):
    route = mock_messages(hibp_clean, anthropic_text_body("done"))
    calls = (
        ToolCall(id="toolu_a", name="web_search", arguments={"query": "a"}),
        ToolCall(id="toolu_b", name="fetch_url", arguments={"url": "https://b"}),
    )
    await collect(
        make_client().stream(
            model="claude-test",
            system="s",
            messages=[
                ChatMessage(role="user", content="hi"),
                ChatMessage(role="assistant", content="Checking.", tool_calls=calls),
                ChatMessage(role="tool", content="found a", tool_call_id="toolu_a"),
                ChatMessage(role="tool", content="found b", tool_call_id="toolu_b"),
            ],
            max_tokens=64,
        )
    )
    body = json.loads(route.calls.last.request.read())
    assert body["messages"][1] == {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "Checking."},
            {"type": "tool_use", "id": "toolu_a", "name": "web_search", "input": {"query": "a"}},
            {
                "type": "tool_use",
                "id": "toolu_b",
                "name": "fetch_url",
                "input": {"url": "https://b"},
            },
        ],
    }
    assert body["messages"][2] == {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "toolu_a", "content": "found a"},
            {"type": "tool_result", "tool_use_id": "toolu_b", "content": "found b"},
        ],
    }


async def test_usage_survives_without_message_delta(hibp_clean: respx.MockRouter):
    body = anthropic_sse(
        anthropic_message_start(input_tokens=6),
        *anthropic_text_block(0, "hey"),
        {"type": "message_stop"},
    )
    mock_messages(hibp_clean, body)
    events = await collect(
        make_client().stream(
            model="claude-test",
            system="s",
            messages=[ChatMessage(role="user", content="hi")],
            max_tokens=64,
        )
    )
    assert Usage(input_tokens=6, output_tokens=1) in events


async def test_provider_refusal_becomes_provider_unavailable(hibp_clean: respx.MockRouter):
    # 401 is not retried by the SDK — the fastest APIStatusError to provoke.
    hibp_clean.post(URL).mock(
        return_value=Response(401, json={"error": {"type": "authentication_error", "message": "x"}})
    )
    with pytest.raises(ProviderUnavailableError, match="HTTP 401"):
        await collect(
            make_client().stream(
                model="claude-test",
                system="s",
                messages=[ChatMessage(role="user", content="hi")],
                max_tokens=64,
            )
        )
