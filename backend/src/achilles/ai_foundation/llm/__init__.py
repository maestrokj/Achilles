"""LLM chat layer: dialect clients, provider factory, tool-calling harness."""

from achilles.ai_foundation.llm.anthropic_client import AnthropicChatClient
from achilles.ai_foundation.llm.factory import client_for
from achilles.ai_foundation.llm.harness import (
    HarnessEvent,
    HarnessTool,
    ToolRoundStart,
    run_turn,
)
from achilles.ai_foundation.llm.openai_client import OpenAIChatClient
from achilles.ai_foundation.llm.types import (
    ChatClient,
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
)

__all__ = [
    "AnthropicChatClient",
    "ChatClient",
    "ChatMessage",
    "HarnessEvent",
    "HarnessTool",
    "OpenAIChatClient",
    "ProviderUnavailableError",
    "StreamEnd",
    "StreamEvent",
    "TextDelta",
    "ToolCall",
    "ToolCallsReady",
    "ToolChoice",
    "ToolRoundStart",
    "ToolSpec",
    "Usage",
    "client_for",
    "run_turn",
]
