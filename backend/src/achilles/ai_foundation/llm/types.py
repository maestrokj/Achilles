"""Common chat language over both wire dialects (openai / anthropic).

Consumers speak these types only; SDK-specific conversion lives inside the
adapters. A ``ChatClient`` is one streaming call — the tool-calling loop on
top of it is ``harness.run_turn``.
"""

import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol, Self

from achilles.ai_foundation.tools.base import ToolManifest

type Role = Literal["user", "assistant", "tool"]

# "none" keeps the catalog on the wire (anthropic rejects tool_use/tool_result
# history without it) while forbidding new calls — the mandatory final answer.
type ToolChoice = Literal["auto", "none"]


class ProviderUnavailableError(Exception):
    """The provider refused or never answered the call (network, auth, 5xx).

    Adapters normalize their SDK exceptions into this one so consumers can
    tell "the provider is down" from a bug — str(exc) is safe to show.
    """


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """One history entry — the minimal superset of both dialects.

    ``role="tool"`` carries a single tool result (``tool_call_id`` +
    ``content``). openai maps it one-to-one (role="tool" message); the
    anthropic adapter folds consecutive tool messages into one user message
    of tool_result blocks, as its wire format requires.
    """

    role: Role
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()  # assistant only
    tool_call_id: str = ""  # tool only


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """What the model sees: name + description + JSON Schema of arguments."""

    name: str
    description: str
    parameters: dict[str, Any]

    @classmethod
    def from_manifest(cls, manifest: ToolManifest) -> Self:
        """The manifest is the source of the wire contract — nothing added here."""
        return cls(
            name=manifest.name,
            description=manifest.description,
            parameters=manifest.parameters,
        )


@dataclass(frozen=True, slots=True)
class TextDelta:
    text: str


@dataclass(frozen=True, slots=True)
class ToolCallsReady:
    """The stream finished with tool requests; arguments are fully assembled."""

    calls: tuple[ToolCall, ...]


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class StreamEnd:
    pass


type StreamEvent = TextDelta | ToolCallsReady | Usage | StreamEnd


class ChatClient(Protocol):
    """One streaming model call in the common language."""

    def stream(
        self,
        *,
        model: str,
        system: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] | None = None,
        tool_choice: ToolChoice = "auto",
        max_tokens: int,
    ) -> AsyncIterator[StreamEvent]: ...

    async def aclose(self) -> None:
        """Release the underlying connection pool when the turn is over."""
        ...


def parse_tool_arguments(raw: str) -> dict[str, Any]:
    """Assembled argument fragments → dict; malformed JSON degrades to {}.

    An empty/broken payload becomes an empty call — the handler's error
    answer reaches the model instead of the whole turn crashing.
    """
    if not raw:
        return {}
    try:
        parsed: Any = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
