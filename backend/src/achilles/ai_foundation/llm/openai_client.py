"""OpenAI-dialect chat client: openai / openai_compatible / google / ollama.

chat.completions with ``stream=True``; tool-call fragments (index / id /
arguments pieces) are assembled here and surface as one ToolCallsReady.
Usage arrives in a tail chunk only when the upstream honours
``stream_options.include_usage`` — when it doesn't, there is no Usage event.
"""

import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import cast

from openai import APIConnectionError, APIStatusError, AsyncOpenAI, omit
from openai.types.chat import (
    ChatCompletionChunk,
    ChatCompletionMessageParam,
    ChatCompletionToolParam,
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


def _to_wire(message: ChatMessage) -> ChatCompletionMessageParam:
    if message.role == "tool":
        return {"role": "tool", "tool_call_id": message.tool_call_id, "content": message.content}
    if message.role == "assistant":
        if message.tool_calls:
            return cast(
                "ChatCompletionMessageParam",
                {
                    "role": "assistant",
                    "content": message.content or None,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(call.arguments),
                            },
                        }
                        for call in message.tool_calls
                    ],
                },
            )
        return {"role": "assistant", "content": message.content}
    return {"role": "user", "content": message.content}


def _tool_param(spec: ToolSpec) -> ChatCompletionToolParam:
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
        },
    }


def _chunk_usage(chunk: ChatCompletionChunk) -> Usage | None:
    if chunk.usage is None:
        return None
    return Usage(
        input_tokens=chunk.usage.prompt_tokens, output_tokens=chunk.usage.completion_tokens
    )


@dataclass(slots=True)
class _PartialCall:
    """Accumulator for one fragmented tool call, keyed by its stream index."""

    id: str = ""
    name: str = ""
    argument_parts: list[str] = field(default_factory=list)

    def as_call(self) -> ToolCall:
        return ToolCall(
            id=self.id, name=self.name, arguments=parse_tool_arguments("".join(self.argument_parts))
        )


class OpenAIChatClient:
    """ChatClient over the chat.completions dialect.

    ``modern_token_param`` picks ``max_completion_tokens`` over the legacy
    ``max_tokens``: openai.com rejects the legacy name on reasoning-era models
    (o-series, gpt-5), while compatible upstreams often accept only the legacy
    one — so the factory decides per adapter.
    """

    def __init__(self, *, base_url: str, api_key: str, modern_token_param: bool = False) -> None:
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        self._modern_token_param = modern_token_param

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
        wire_messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system},
            *(_to_wire(message) for message in messages),
        ]
        try:
            response = await self._client.chat.completions.create(
                model=model,
                messages=wire_messages,
                max_tokens=omit if self._modern_token_param else max_tokens,
                max_completion_tokens=max_tokens if self._modern_token_param else omit,
                stream=True,
                stream_options={"include_usage": True},
                tools=[_tool_param(spec) for spec in tools] if tools else omit,
                tool_choice="none" if tools and tool_choice == "none" else omit,
            )
        except APIConnectionError as exc:
            raise ProviderUnavailableError("provider unreachable") from exc
        except APIStatusError as exc:
            raise ProviderUnavailableError(f"provider returned HTTP {exc.status_code}") from exc

        partial: dict[int, _PartialCall] = {}
        usage: Usage | None = None
        async for chunk in response:
            usage = _chunk_usage(chunk) or usage
            for choice in chunk.choices:
                if choice.delta.content:
                    yield TextDelta(choice.delta.content)
                for fragment in choice.delta.tool_calls or ():
                    slot = partial.setdefault(fragment.index, _PartialCall())
                    if fragment.id:
                        slot.id = fragment.id
                    if fragment.function and fragment.function.name:
                        slot.name = fragment.function.name
                    if fragment.function and fragment.function.arguments:
                        slot.argument_parts.append(fragment.function.arguments)
        if partial:
            yield ToolCallsReady(tuple(partial[index].as_call() for index in sorted(partial)))
        if usage is not None:
            yield usage
        yield StreamEnd()
