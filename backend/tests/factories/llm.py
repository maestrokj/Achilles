"""Scripted ChatClient shared by the harness and agent-loop suites.

One event list per stream() call, in order; every call is recorded with its
kwargs so tests assert on what reached the wire seam — no network involved.
"""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field

from achilles.ai_foundation.llm.types import (
    ChatMessage,
    StreamEnd,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolCallsReady,
    ToolChoice,
    ToolSpec,
    Usage,
)


@dataclass
class RecordedStream:
    model: str
    system: str
    messages: list[ChatMessage]
    tools: list[ToolSpec] | None
    tool_choice: ToolChoice
    max_tokens: int


@dataclass
class FakeChatClient:
    """One scripted event list per stream() call, in order."""

    rounds: list[list[StreamEvent]]
    calls: list[RecordedStream] = field(default_factory=list)
    closed: bool = False

    async def stream(
        self,
        *,
        model: str,
        system: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] | None = None,
        tool_choice: ToolChoice = "auto",
        max_tokens: int,
    ) -> AsyncIterator[StreamEvent]:
        self.calls.append(
            RecordedStream(
                model=model,
                system=system,
                messages=list(messages),
                tools=list(tools) if tools is not None else None,
                tool_choice=tool_choice,
                max_tokens=max_tokens,
            )
        )
        if not self.rounds:
            # Loud on purpose: a silent fallback would hide a regression that
            # adds an unplanned extra LLM round (an extra billable call).
            msg = "FakeChatClient: unexpected extra stream() round"
            raise AssertionError(msg)
        for event in self.rounds.pop(0):
            yield event

    async def aclose(self) -> None:
        self.closed = True


def answer_round(text: str, *, usage: tuple[int, int] = (10, 5)) -> list[StreamEvent]:
    return [
        TextDelta(text),
        Usage(input_tokens=usage[0], output_tokens=usage[1]),
        StreamEnd(),
    ]


def call_round(*calls: ToolCall, usage: tuple[int, int] = (10, 5)) -> list[StreamEvent]:
    return [
        ToolCallsReady(tuple(calls)),
        Usage(input_tokens=usage[0], output_tokens=usage[1]),
        StreamEnd(),
    ]
