"""Tool-calling harness: describe tools → dispatch calls → feed results back.

The chat contract is a single round (tool-catalog.html): stream #1 with
tools; if the model asked for calls, execute them all in parallel and run
stream #2 with tool_choice="none" — the mandatory final answer, no loop.
The catalog stays on the wire in round 2 (anthropic rejects tool_use /
tool_result history without it); "none" is what forbids further calls.
Agent Engine (stage 6) builds its iteration-capped loop from the same
ChatClient + dispatch pieces rather than a hidden flag here.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass

from achilles.ai_foundation.llm.types import (
    ChatClient,
    ChatMessage,
    StreamEnd,
    TextDelta,
    ToolCall,
    ToolCallsReady,
    ToolSpec,
    Usage,
)


@dataclass(frozen=True, slots=True)
class HarnessTool:
    """A tool the model may call; the handler is invoked as handler(**arguments)."""

    spec: ToolSpec
    handler: Callable[..., Awaitable[object]]


@dataclass(frozen=True, slots=True)
class ToolRoundStart:
    """The model requested tools; the final answer comes after this round."""

    calls: tuple[ToolCall, ...]


type HarnessEvent = TextDelta | ToolRoundStart | Usage | StreamEnd


async def run_turn(
    client: ChatClient,
    *,
    model: str,
    system: str,
    messages: Sequence[ChatMessage],
    tools: Sequence[HarnessTool],
    max_tokens: int,
) -> AsyncIterator[HarnessEvent]:
    """One chat turn; yields text deltas, the tool round, total Usage, StreamEnd."""
    total_input = 0
    total_output = 0
    saw_usage = False
    calls: tuple[ToolCall, ...] = ()
    spoken: list[str] = []

    specs = [tool.spec for tool in tools] or None
    async for event in client.stream(
        model=model, system=system, messages=messages, tools=specs, max_tokens=max_tokens
    ):
        match event:
            case TextDelta(text):
                spoken.append(text)
                yield event
            case ToolCallsReady(ready):
                calls = ready
            case Usage(input_tokens, output_tokens):
                saw_usage = True
                total_input += input_tokens
                total_output += output_tokens
            case StreamEnd():
                pass

    if calls:
        yield ToolRoundStart(calls)
        by_name = {tool.spec.name: tool for tool in tools}
        results = await asyncio.gather(*(dispatch(by_name, call) for call in calls))
        history = [
            *messages,
            ChatMessage(role="assistant", content="".join(spoken), tool_calls=calls),
            *(
                ChatMessage(role="tool", content=result, tool_call_id=call.id)
                for call, result in zip(calls, results, strict=True)
            ),
        ]
        async for event in client.stream(
            model=model,
            system=system,
            messages=history,
            tools=specs,
            tool_choice="none",
            max_tokens=max_tokens,
        ):
            match event:
                case TextDelta():
                    yield event
                case Usage(input_tokens, output_tokens):
                    saw_usage = True
                    total_input += input_tokens
                    total_output += output_tokens
                case ToolCallsReady() | StreamEnd():
                    pass  # calling is off this round — a stray request has nowhere to go

    if saw_usage:
        yield Usage(input_tokens=total_input, output_tokens=total_output)
    yield StreamEnd()


async def dispatch(tools_by_name: Mapping[str, HarnessTool], call: ToolCall) -> str:
    """Execute one call; any failure becomes a text result for the model, never a 500."""
    tool = tools_by_name.get(call.name)
    if tool is None:
        return f"Error: unknown tool '{call.name}'"
    try:
        result = await tool.handler(**call.arguments)
    except Exception as exc:  # the model recovers from tool errors, the turn must not die
        return f"Error: {type(exc).__name__}: {exc}"
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False, default=str)
