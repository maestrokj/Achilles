"""The iteration-capped agent loop over ChatClient + dispatch (harness.html).

The same primitives as the chat harness, a different stop condition: the
model keeps calling tools round after round until it answers without calls
or hits the platform iteration cap (a spin guard, not a budget —
runtime.html#loop). Usage accumulates across every round.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

from achilles.ai_foundation.llm.harness import HarnessTool, dispatch
from achilles.ai_foundation.llm.types import (
    ChatClient,
    ChatMessage,
    StreamEnd,
    TextDelta,
    ToolCall,
    ToolCallsReady,
    Usage,
)


@dataclass(frozen=True, slots=True)
class LoopOutcome:
    output: str  # the final answer; on hit_cap — the last round's partial text
    iterations: int
    input_tokens: int
    output_tokens: int
    hit_cap: bool


async def run_loop(
    client: ChatClient,
    *,
    model: str,
    system: str,
    messages: Sequence[ChatMessage],
    tools: Sequence[HarnessTool],
    iteration_cap: int,
    max_tokens: int,
) -> LoopOutcome:
    """Rounds of stream → dispatch → feed results back, until an answer or the cap."""
    history = list(messages)
    specs = [tool.spec for tool in tools] or None
    by_name = {tool.spec.name: tool for tool in tools}
    total_input = 0
    total_output = 0
    spoken: list[str] = []

    for iteration in range(1, iteration_cap + 1):
        spoken = []
        calls: tuple[ToolCall, ...] = ()
        async for event in client.stream(
            model=model, system=system, messages=history, tools=specs, max_tokens=max_tokens
        ):
            match event:
                case TextDelta(text):
                    spoken.append(text)
                case ToolCallsReady(ready):
                    calls = ready
                case Usage(input_tokens, output_tokens):
                    total_input += input_tokens
                    total_output += output_tokens
                case StreamEnd():
                    pass
        if not calls:
            return LoopOutcome(
                output="".join(spoken),
                iterations=iteration,
                input_tokens=total_input,
                output_tokens=total_output,
                hit_cap=False,
            )
        results = await asyncio.gather(*(dispatch(by_name, call) for call in calls))
        history.append(ChatMessage(role="assistant", content="".join(spoken), tool_calls=calls))
        history.extend(
            ChatMessage(role="tool", content=result, tool_call_id=call.id)
            for call, result in zip(calls, results, strict=True)
        )

    return LoopOutcome(
        output="".join(spoken),
        iterations=iteration_cap,
        input_tokens=total_input,
        output_tokens=total_output,
        hit_cap=True,
    )
