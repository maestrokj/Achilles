"""SSE wire builders shared by the LLM client/factory tests."""

import json
from collections.abc import AsyncIterator
from typing import Any

from achilles.ai_foundation.llm.types import StreamEvent


async def collect(stream: AsyncIterator[StreamEvent]) -> list[StreamEvent]:
    return [event async for event in stream]


# --- OpenAI dialect -----------------------------------------------------------


def openai_sse(*payloads: dict[str, Any]) -> bytes:
    frames = [f"data: {json.dumps(payload)}\n\n" for payload in payloads]
    frames.append("data: [DONE]\n\n")
    return "".join(frames).encode()


def openai_chunk(
    *,
    delta: dict[str, Any] | None = None,
    finish: str | None = None,
    usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    chunk: dict[str, Any] = {
        "id": "chunk-1",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "test-model",
        "choices": [],
    }
    if delta is not None or finish is not None:
        chunk["choices"] = [{"index": 0, "delta": delta or {}, "finish_reason": finish}]
    if usage is not None:
        chunk["usage"] = usage
    return chunk


def openai_text_body(*texts: str, usage: dict[str, int] | None = None) -> bytes:
    chunks = [openai_chunk(delta={"content": text}) for text in texts]
    chunks.append(openai_chunk(finish="stop"))
    if usage is not None:
        chunks.append(openai_chunk(usage=usage))
    return openai_sse(*chunks)


# --- Anthropic dialect --------------------------------------------------------


def anthropic_sse(*events: dict[str, Any]) -> bytes:
    return "".join(
        f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events
    ).encode()


def anthropic_message_start(*, input_tokens: int = 10) -> dict[str, Any]:
    return {
        "type": "message_start",
        "message": {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "test-model",
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": input_tokens, "output_tokens": 1},
        },
    }


def anthropic_text_block(index: int, *texts: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = [
        {
            "type": "content_block_start",
            "index": index,
            "content_block": {"type": "text", "text": ""},
        }
    ]
    events.extend(
        {
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "text_delta", "text": text},
        }
        for text in texts
    )
    events.append({"type": "content_block_stop", "index": index})
    return events


def anthropic_tool_block(
    index: int, *, id: str, name: str, fragments: tuple[str, ...]
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = [
        {
            "type": "content_block_start",
            "index": index,
            "content_block": {"type": "tool_use", "id": id, "name": name, "input": {}},
        }
    ]
    events.extend(
        {
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "input_json_delta", "partial_json": fragment},
        }
        for fragment in fragments
    )
    events.append({"type": "content_block_stop", "index": index})
    return events


def anthropic_tail(
    *, output_tokens: int = 5, stop_reason: str = "end_turn"
) -> list[dict[str, Any]]:
    return [
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": output_tokens},
        },
        {"type": "message_stop"},
    ]


def anthropic_text_body(*texts: str, input_tokens: int = 10, output_tokens: int = 5) -> bytes:
    return anthropic_sse(
        anthropic_message_start(input_tokens=input_tokens),
        *anthropic_text_block(0, *texts),
        *anthropic_tail(output_tokens=output_tokens),
    )
