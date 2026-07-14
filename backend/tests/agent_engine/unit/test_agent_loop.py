"""The iteration-capped loop over ChatClient + dispatch (P0, harness.html)."""

import pytest

from achilles.agent_engine.runtime.loop import run_loop
from achilles.ai_foundation.llm.harness import HarnessTool
from achilles.ai_foundation.llm.types import ChatMessage, ToolCall, ToolSpec
from tests.factories.llm import FakeChatClient, answer_round, call_round

pytestmark = [pytest.mark.unit, pytest.mark.p0]

KICKOFF = [ChatMessage(role="user", content="go")]


def echo_tool(seen: list[dict[str, object]]) -> HarnessTool:
    async def handler(**arguments: object) -> str:
        seen.append(arguments)
        return f"echo:{arguments.get('x')}"

    return HarnessTool(
        spec=ToolSpec(name="echo", description="", parameters={"type": "object"}),
        handler=handler,
    )


async def test_answer_without_calls_finishes_in_one_round() -> None:
    client = FakeChatClient(rounds=[answer_round("final report")])

    outcome = await run_loop(
        client, model="m", system="s", messages=KICKOFF, tools=[], iteration_cap=5, max_tokens=100
    )

    assert outcome.output == "final report"
    assert outcome.iterations == 1
    assert outcome.hit_cap is False
    assert len(client.calls) == 1


async def test_tool_round_feeds_result_back_as_data() -> None:
    seen: list[dict[str, object]] = []
    client = FakeChatClient(
        rounds=[
            call_round(ToolCall(id="c1", name="echo", arguments={"x": "42"})),
            answer_round("done"),
        ]
    )

    outcome = await run_loop(
        client,
        model="m",
        system="s",
        messages=KICKOFF,
        tools=[echo_tool(seen)],
        iteration_cap=5,
        max_tokens=100,
    )

    assert outcome.output == "done"
    assert outcome.iterations == 2
    assert seen == [{"x": "42"}]
    # Round 2 saw the assistant call + the tool result appended to history.
    history = client.calls[1].messages
    assert history[-2].role == "assistant"
    assert history[-2].tool_calls[0].name == "echo"
    assert history[-1].role == "tool"
    assert history[-1].content == "echo:42"
    assert history[-1].tool_call_id == "c1"


async def test_context_accumulates_across_rounds() -> None:
    seen: list[dict[str, object]] = []
    client = FakeChatClient(
        rounds=[
            call_round(ToolCall(id="c1", name="echo", arguments={"x": "1"})),
            call_round(ToolCall(id="c2", name="echo", arguments={"x": "2"})),
            answer_round("summary"),
        ]
    )

    outcome = await run_loop(
        client,
        model="m",
        system="s",
        messages=KICKOFF,
        tools=[echo_tool(seen)],
        iteration_cap=5,
        max_tokens=100,
    )

    assert outcome.iterations == 3
    assert [c.get("x") for c in seen] == ["1", "2"]
    # The third round carries both prior tool exchanges (1 + 2*2 messages).
    assert len(client.calls[2].messages) == 5


async def test_iteration_cap_stops_a_spinning_agent() -> None:
    call = ToolCall(id="c", name="echo", arguments={"x": "loop"})
    client = FakeChatClient(rounds=[call_round(call), call_round(call), call_round(call)])

    outcome = await run_loop(
        client,
        model="m",
        system="s",
        messages=KICKOFF,
        tools=[echo_tool([])],
        iteration_cap=3,
        max_tokens=100,
    )

    assert outcome.hit_cap is True
    assert outcome.iterations == 3
    assert len(client.calls) == 3  # the cap cut the next round, not the current one


async def test_unknown_tool_becomes_error_text_not_crash() -> None:
    client = FakeChatClient(
        rounds=[
            call_round(ToolCall(id="c1", name="ghost", arguments={})),
            answer_round("recovered"),
        ]
    )

    outcome = await run_loop(
        client, model="m", system="s", messages=KICKOFF, tools=[], iteration_cap=5, max_tokens=100
    )

    assert outcome.output == "recovered"
    assert "unknown tool" in client.calls[1].messages[-1].content


async def test_tool_exception_becomes_error_text() -> None:
    async def broken(**arguments: object) -> str:
        raise ValueError("boom")

    tool = HarnessTool(
        spec=ToolSpec(name="broken", description="", parameters={"type": "object"}),
        handler=broken,
    )
    client = FakeChatClient(
        rounds=[
            call_round(ToolCall(id="c1", name="broken", arguments={})),
            answer_round("still alive"),
        ]
    )

    outcome = await run_loop(
        client,
        model="m",
        system="s",
        messages=KICKOFF,
        tools=[tool],
        iteration_cap=5,
        max_tokens=100,
    )

    assert outcome.output == "still alive"
    assert "Error: ValueError: boom" in client.calls[1].messages[-1].content


async def test_usage_accumulates_across_rounds() -> None:
    client = FakeChatClient(
        rounds=[
            call_round(ToolCall(id="c1", name="ghost", arguments={}), usage=(100, 10)),
            answer_round("done", usage=(200, 30)),
        ]
    )

    outcome = await run_loop(
        client, model="m", system="s", messages=KICKOFF, tools=[], iteration_cap=5, max_tokens=100
    )

    assert outcome.input_tokens == 300
    assert outcome.output_tokens == 40
