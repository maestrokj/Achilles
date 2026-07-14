"""Anthropic-dialect chat client (Messages API, raw SSE events).

Text comes from content_block_delta/text_delta; tool_use blocks assemble
their JSON arguments from input_json_delta fragments. Usage is split across
message_start (input) and message_delta (output).
"""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field

from anthropic import APIConnectionError, APIStatusError, AsyncAnthropic, omit
from anthropic.types import (
    MessageParam,
    TextBlockParam,
    ToolParam,
    ToolResultBlockParam,
    ToolUseBlockParam,
)

from achilles.ai_foundation.llm.types import (
    ChatMessage,
    ProviderUnavailableError,
    StreamEnd,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolCallsReady,
    ToolChoice,
    ToolSpec,
    Usage,
    parse_tool_arguments,
)


def _to_wire(messages: Sequence[ChatMessage]) -> list[MessageParam]:
    """Fold the neutral history into anthropic turns.

    Consecutive tool messages become ONE user message of tool_result blocks —
    parallel calls must be answered in a single turn on this dialect.
    """
    wire: list[MessageParam] = []
    results: list[ToolResultBlockParam] = []

    def flush_results() -> None:
        if results:
            wire.append({"role": "user", "content": list(results)})
            results.clear()

    for message in messages:
        if message.role == "tool":
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": message.tool_call_id,
                    "content": message.content,
                }
            )
            continue
        flush_results()
        if message.role == "assistant" and message.tool_calls:
            blocks: list[TextBlockParam | ToolUseBlockParam] = []
            if message.content:
                blocks.append({"type": "text", "text": message.content})
            blocks.extend(
                {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
                for call in message.tool_calls
            )
            wire.append({"role": "assistant", "content": blocks})
        else:
            wire.append({"role": message.role, "content": message.content})
    flush_results()
    return wire


def _tool_param(spec: ToolSpec) -> ToolParam:
    return {"name": spec.name, "description": spec.description, "input_schema": spec.parameters}


@dataclass(slots=True)
class _PartialBlock:
    """Accumulator for one tool_use block, keyed by its content index."""

    id: str
    name: str
    argument_parts: list[str] = field(default_factory=list)

    def as_call(self) -> ToolCall:
        return ToolCall(
            id=self.id, name=self.name, arguments=parse_tool_arguments("".join(self.argument_parts))
        )


class AnthropicChatClient:
    """ChatClient over the anthropic Messages dialect."""

    def __init__(self, *, api_key: str, base_url: str | None = None) -> None:
        self._client = AsyncAnthropic(api_key=api_key, base_url=base_url)

    async def aclose(self) -> None:
        await self._client.close()

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
        wire_messages = _to_wire(messages)
        try:
            response = await self._client.messages.create(
                model=model,
                system=system,
                messages=wire_messages,
                max_tokens=max_tokens,
                tools=[_tool_param(spec) for spec in tools] if tools else omit,
                tool_choice={"type": "none"} if tools and tool_choice == "none" else omit,
                stream=True,
            )
        except APIConnectionError as exc:
            raise ProviderUnavailableError("provider unreachable") from exc
        except APIStatusError as exc:
            raise ProviderUnavailableError(f"provider returned HTTP {exc.status_code}") from exc

        blocks: dict[int, _PartialBlock] = {}
        input_tokens = 0
        output_tokens = 0
        saw_usage = False
        async for event in response:
            if event.type == "message_start":
                input_tokens = event.message.usage.input_tokens
                output_tokens = event.message.usage.output_tokens
                saw_usage = True
            elif event.type == "content_block_start":
                if event.content_block.type == "tool_use":
                    blocks[event.index] = _PartialBlock(
                        id=event.content_block.id, name=event.content_block.name
                    )
            elif event.type == "content_block_delta":
                if event.delta.type == "text_delta":
                    yield TextDelta(event.delta.text)
                elif event.delta.type == "input_json_delta" and (slot := blocks.get(event.index)):
                    slot.argument_parts.append(event.delta.partial_json)
            elif event.type == "message_delta":
                output_tokens = event.usage.output_tokens
        if blocks:
            yield ToolCallsReady(tuple(blocks[index].as_call() for index in sorted(blocks)))
        if saw_usage:
            yield Usage(input_tokens=input_tokens, output_tokens=output_tokens)
        yield StreamEnd()
