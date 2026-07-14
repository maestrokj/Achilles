"""OpenAIChatClient: SSE stream → common events (unit).

respx layering follows the embeddings tests: routes are added on the
conftest's autouse egress-guard router instead of a second respx layer.
"""

import json

import pytest
import respx
from httpx import Response

from achilles.ai_foundation.llm import ChatMessage, OpenAIChatClient, ToolCall, ToolSpec
from achilles.ai_foundation.llm.types import (
    ProviderUnavailableError,
    StreamEnd,
    TextDelta,
    ToolCallsReady,
    Usage,
)
from tests.ai_foundation.unit.llm_wire import collect, openai_chunk, openai_sse, openai_text_body

pytestmark = [pytest.mark.unit, pytest.mark.p1]

BASE = "https://llm.test/v1"
URL = f"{BASE}/chat/completions"

SEARCH_SPEC = ToolSpec(
    name="web_search",
    description="Search the web.",
    parameters={"type": "object", "properties": {"query": {"type": "string"}}},
)


def make_client() -> OpenAIChatClient:
    return OpenAIChatClient(base_url=BASE, api_key="test-key")


def mock_completions(router: respx.MockRouter, body: bytes) -> respx.Route:
    return router.post(URL).mock(
        return_value=Response(200, content=body, headers={"content-type": "text/event-stream"})
    )


def tool_fragment_chunk(index: int, **function: str) -> dict[str, object]:
    fragment: dict[str, object] = {"index": index, "function": function}
    return openai_chunk(delta={"tool_calls": [fragment]})


async def test_plain_text_stream(hibp_clean: respx.MockRouter):
    route = mock_completions(hibp_clean, openai_text_body("Hel", "lo"))
    events = await collect(
        make_client().stream(
            model="gpt-test",
            system="Be terse.",
            messages=[ChatMessage(role="user", content="hi")],
            max_tokens=128,
        )
    )
    assert events == [TextDelta("Hel"), TextDelta("lo"), StreamEnd()]
    body = json.loads(route.calls.last.request.read())
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    assert body["max_tokens"] == 128
    assert body["messages"][0] == {"role": "system", "content": "Be terse."}
    assert "tools" not in body


async def test_tools_are_wired_as_functions(hibp_clean: respx.MockRouter):
    route = mock_completions(hibp_clean, openai_text_body("ok"))
    await collect(
        make_client().stream(
            model="gpt-test",
            system="s",
            messages=[ChatMessage(role="user", content="hi")],
            tools=[SEARCH_SPEC],
            max_tokens=64,
        )
    )
    body = json.loads(route.calls.last.request.read())
    assert body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search the web.",
                "parameters": SEARCH_SPEC.parameters,
            },
        }
    ]
    assert "tool_choice" not in body  # "auto" is the upstream default — not spelled out


async def test_tool_choice_none_forbids_calls_but_keeps_catalog(hibp_clean: respx.MockRouter):
    route = mock_completions(hibp_clean, openai_text_body("ok"))
    await collect(
        make_client().stream(
            model="gpt-test",
            system="s",
            messages=[ChatMessage(role="user", content="hi")],
            tools=[SEARCH_SPEC],
            tool_choice="none",
            max_tokens=64,
        )
    )
    body = json.loads(route.calls.last.request.read())
    assert body["tool_choice"] == "none"
    assert [t["function"]["name"] for t in body["tools"]] == ["web_search"]


async def test_modern_token_param_switches_to_max_completion_tokens(
    hibp_clean: respx.MockRouter,
):
    # openai.com rejects the legacy max_tokens on reasoning-era models
    # (o-series, gpt-5); compatible upstreams keep the legacy spelling.
    route = mock_completions(hibp_clean, openai_text_body("ok"))
    await collect(
        OpenAIChatClient(base_url=BASE, api_key="test-key", modern_token_param=True).stream(
            model="gpt-test",
            system="s",
            messages=[ChatMessage(role="user", content="hi")],
            max_tokens=64,
        )
    )
    body = json.loads(route.calls.last.request.read())
    assert body["max_completion_tokens"] == 64
    assert "max_tokens" not in body


async def test_fragmented_tool_call_is_assembled(hibp_clean: respx.MockRouter):
    chunks = [
        openai_chunk(delta={"content": "Looking it up."}),
        openai_chunk(
            delta={
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "web_search", "arguments": ""},
                    }
                ]
            }
        ),
        tool_fragment_chunk(0, arguments='{"query": '),
        tool_fragment_chunk(0, arguments='"cats"}'),
        openai_chunk(finish="tool_calls"),
    ]
    mock_completions(hibp_clean, openai_sse(*chunks))
    events = await collect(
        make_client().stream(
            model="gpt-test",
            system="s",
            messages=[ChatMessage(role="user", content="hi")],
            tools=[SEARCH_SPEC],
            max_tokens=64,
        )
    )
    assert events == [
        TextDelta("Looking it up."),
        ToolCallsReady((ToolCall(id="call_1", name="web_search", arguments={"query": "cats"}),)),
        StreamEnd(),
    ]


async def test_two_tool_calls_keep_index_order(hibp_clean: respx.MockRouter):
    chunks = [
        openai_chunk(
            delta={
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_a",
                        "type": "function",
                        "function": {"name": "web_search", "arguments": '{"query": "a"}'},
                    },
                    {
                        "index": 1,
                        "id": "call_b",
                        "type": "function",
                        "function": {"name": "fetch_url", "arguments": '{"url": "https://b"}'},
                    },
                ]
            }
        ),
        openai_chunk(finish="tool_calls"),
    ]
    mock_completions(hibp_clean, openai_sse(*chunks))
    events = await collect(
        make_client().stream(
            model="gpt-test",
            system="s",
            messages=[ChatMessage(role="user", content="hi")],
            tools=[SEARCH_SPEC],
            max_tokens=64,
        )
    )
    ready = events[0]
    assert isinstance(ready, ToolCallsReady)
    assert ready.calls == (
        ToolCall(id="call_a", name="web_search", arguments={"query": "a"}),
        ToolCall(id="call_b", name="fetch_url", arguments={"url": "https://b"}),
    )


async def test_usage_tail_chunk_becomes_usage_event(hibp_clean: respx.MockRouter):
    usage = {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12}
    mock_completions(hibp_clean, openai_text_body("hey", usage=usage))
    events = await collect(
        make_client().stream(
            model="gpt-test",
            system="s",
            messages=[ChatMessage(role="user", content="hi")],
            max_tokens=64,
        )
    )
    assert events == [TextDelta("hey"), Usage(input_tokens=5, output_tokens=7), StreamEnd()]


async def test_missing_usage_emits_no_usage_event(hibp_clean: respx.MockRouter):
    mock_completions(hibp_clean, openai_text_body("hey"))
    events = await collect(
        make_client().stream(
            model="gpt-test",
            system="s",
            messages=[ChatMessage(role="user", content="hi")],
            max_tokens=64,
        )
    )
    assert not any(isinstance(event, Usage) for event in events)
    assert events[-1] == StreamEnd()


async def test_history_round_trips_tool_turns(hibp_clean: respx.MockRouter):
    route = mock_completions(hibp_clean, openai_text_body("done"))
    call = ToolCall(id="call_1", name="web_search", arguments={"query": "cats"})
    await collect(
        make_client().stream(
            model="gpt-test",
            system="s",
            messages=[
                ChatMessage(role="user", content="hi"),
                ChatMessage(role="assistant", content="Checking.", tool_calls=(call,)),
                ChatMessage(role="tool", content="cats are cats", tool_call_id="call_1"),
            ],
            max_tokens=64,
        )
    )
    body = json.loads(route.calls.last.request.read())
    assert body["messages"][2] == {
        "role": "assistant",
        "content": "Checking.",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "web_search", "arguments": '{"query": "cats"}'},
            }
        ],
    }
    assert body["messages"][3] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "cats are cats",
    }


async def test_provider_refusal_becomes_provider_unavailable(hibp_clean: respx.MockRouter):
    # 401 is not retried by the SDK — the fastest APIStatusError to provoke.
    hibp_clean.post(URL).mock(return_value=Response(401, json={"error": {"message": "bad key"}}))
    with pytest.raises(ProviderUnavailableError, match="HTTP 401"):
        await collect(
            make_client().stream(
                model="gpt-test",
                system="s",
                messages=[ChatMessage(role="user", content="hi")],
                max_tokens=64,
            )
        )
