"""run_turn: one tool round between two streams, errors as text (unit).

A scripted fake ChatClient keeps these tests off the network entirely —
the wire dialects have their own suites.
"""

import asyncio
import json
from typing import Any

import pytest

from achilles.ai_foundation.llm import (
    ChatMessage,
    HarnessTool,
    StreamEnd,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolCallsReady,
    ToolRoundStart,
    ToolSpec,
    Usage,
    run_turn,
)
from tests.factories.llm import FakeChatClient

pytestmark = [pytest.mark.unit, pytest.mark.p1]


def fake_client(*scripts: list[StreamEvent]) -> FakeChatClient:
    return FakeChatClient(rounds=list(scripts))


def spec(name: str) -> ToolSpec:
    return ToolSpec(name=name, description="", parameters={"type": "object"})


def harness_tool(name: str, handler: Any) -> HarnessTool:
    return HarnessTool(spec=spec(name), handler=handler)


async def echo(**kwargs: object) -> object:
    return {"echo": kwargs}


USER = [ChatMessage(role="user", content="hi")]


async def run(fake: FakeChatClient, tools: list[HarnessTool]) -> list[object]:
    return [
        event
        async for event in run_turn(
            fake, model="m", system="s", messages=USER, tools=tools, max_tokens=64
        )
    ]


async def test_text_only_single_stream():
    fake = fake_client([TextDelta("Hi"), Usage(3, 4), StreamEnd()])
    events = await run(fake, [harness_tool("echo", echo)])
    assert events == [TextDelta("Hi"), Usage(3, 4), StreamEnd()]
    assert len(fake.calls) == 1
    assert fake.calls[0].tools == [spec("echo")]


async def test_tool_round_runs_second_stream_with_calling_off():
    call = ToolCall(id="c1", name="echo", arguments={"x": 1})
    fake = fake_client(
        [TextDelta("Let me check."), ToolCallsReady((call,)), Usage(10, 5), StreamEnd()],
        [TextDelta("Answer"), Usage(20, 7), StreamEnd()],
    )
    events = await run(fake, [harness_tool("echo", echo)])
    assert events == [
        TextDelta("Let me check."),
        ToolRoundStart((call,)),
        TextDelta("Answer"),
        Usage(input_tokens=30, output_tokens=12),
        StreamEnd(),
    ]
    assert len(fake.calls) == 2
    assert fake.calls[0].tool_choice == "auto"
    # Round 2 keeps the catalog on the wire (anthropic requires it alongside
    # tool_use/tool_result history) but forbids further calls.
    assert fake.calls[1].tools == [spec("echo")]
    assert fake.calls[1].tool_choice == "none"
    history = fake.calls[1].messages
    assert history[0] == USER[0]
    assert history[1] == ChatMessage(role="assistant", content="Let me check.", tool_calls=(call,))
    assert history[2] == ChatMessage(
        role="tool", content=json.dumps({"echo": {"x": 1}}), tool_call_id="c1"
    )


async def test_parallel_calls_run_concurrently():
    barrier = asyncio.Barrier(2)

    async def meet(**_: object) -> str:
        # Sequential execution would deadlock here; the timeout guards the suite.
        await barrier.wait()
        return "met"

    calls = (
        ToolCall(id="c1", name="meet", arguments={}),
        ToolCall(id="c2", name="meet", arguments={}),
    )
    fake = fake_client(
        [ToolCallsReady(calls), StreamEnd()],
        [TextDelta("both done"), StreamEnd()],
    )
    async with asyncio.timeout(5):
        events = await run(fake, [harness_tool("meet", meet)])
    assert ToolRoundStart(calls) in events
    history = fake.calls[1].messages
    assert [m.content for m in history if m.role == "tool"] == ["met", "met"]


async def test_handler_exception_becomes_error_text():
    async def boom(**_: object) -> str:
        raise ValueError("no such page")

    call = ToolCall(id="c1", name="boom", arguments={})
    fake = fake_client(
        [ToolCallsReady((call,)), StreamEnd()],
        [TextDelta("Sorry."), StreamEnd()],
    )
    events = await run(fake, [harness_tool("boom", boom)])
    assert TextDelta("Sorry.") in events
    tool_message = fake.calls[1].messages[-1]
    assert tool_message.role == "tool"
    assert tool_message.content == "Error: ValueError: no such page"


async def test_unknown_tool_name_becomes_error_text():
    call = ToolCall(id="c1", name="nope", arguments={"x": 1})
    fake = fake_client(
        [ToolCallsReady((call,)), StreamEnd()],
        [StreamEnd()],
    )
    await run(fake, [harness_tool("echo", echo)])
    tool_message = fake.calls[1].messages[-1]
    assert tool_message.content == "Error: unknown tool 'nope'"


async def test_str_result_passes_through_unserialized():
    async def plain(**_: object) -> str:
        return "already text"

    call = ToolCall(id="c1", name="plain", arguments={})
    fake = fake_client([ToolCallsReady((call,)), StreamEnd()], [StreamEnd()])
    await run(fake, [harness_tool("plain", plain)])
    assert fake.calls[1].messages[-1].content == "already text"


async def test_arguments_are_spread_as_kwargs():
    seen: dict[str, object] = {}

    async def capture(**kwargs: object) -> str:
        seen.update(kwargs)
        return "ok"

    call = ToolCall(id="c1", name="capture", arguments={"query": "cats", "limit": 3})
    fake = fake_client([ToolCallsReady((call,)), StreamEnd()], [StreamEnd()])
    await run(fake, [harness_tool("capture", capture)])
    assert seen == {"query": "cats", "limit": 3}


async def test_no_usage_anywhere_means_no_usage_event():
    call = ToolCall(id="c1", name="echo", arguments={})
    fake = fake_client(
        [ToolCallsReady((call,)), StreamEnd()],
        [TextDelta("done"), StreamEnd()],
    )
    events = await run(fake, [harness_tool("echo", echo)])
    assert not any(isinstance(event, Usage) for event in events)


async def test_usage_from_single_stream_still_reported():
    call = ToolCall(id="c1", name="echo", arguments={})
    fake = fake_client(
        [ToolCallsReady((call,)), Usage(10, 5), StreamEnd()],
        [TextDelta("done"), StreamEnd()],
    )
    events = await run(fake, [harness_tool("echo", echo)])
    assert Usage(input_tokens=10, output_tokens=5) in events
