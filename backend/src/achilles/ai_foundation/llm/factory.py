"""Provider row → ChatClient: adapter dispatch + API-key decryption.

Base URLs follow discovery's convention — the stored/base host carries no
API path, so the dialect path (/v1, Gemini's OpenAI-compatible
/v1beta/openai) is appended here. Key problems surface as 502
PROVIDER_UNREACHABLE via discovery's shared helpers — never a 500.
"""

from achilles.ai_foundation.constants import ProviderAdapter
from achilles.ai_foundation.llm.anthropic_client import AnthropicChatClient
from achilles.ai_foundation.llm.openai_client import OpenAIChatClient
from achilles.ai_foundation.llm.types import ChatClient
from achilles.ai_foundation.models import AiProvider
from achilles.ai_foundation.services.discovery import decrypted_api_key, resolve_base_url

# Both SDKs require a non-empty key string even for keyless upstreams (ollama).
_KEYLESS_PLACEHOLDER = "keyless"

_OPENAI_PATH = "/v1"
_GOOGLE_OPENAI_PATH = "/v1beta/openai"  # Gemini's OpenAI-compatible endpoint


def client_for(provider: AiProvider, *, crypto_key: bytes) -> ChatClient:
    api_key = decrypted_api_key(provider, key=crypto_key)
    adapter = ProviderAdapter(provider.adapter)
    if adapter is ProviderAdapter.ANTHROPIC:
        return AnthropicChatClient(
            api_key=api_key or _KEYLESS_PLACEHOLDER, base_url=provider.base_url
        )
    path = _GOOGLE_OPENAI_PATH if adapter is ProviderAdapter.GOOGLE else _OPENAI_PATH
    return OpenAIChatClient(
        base_url=resolve_base_url(provider) + path,
        api_key=api_key or _KEYLESS_PLACEHOLDER,
        # Only openai.com needs (and reliably supports) max_completion_tokens.
        modern_token_param=adapter is ProviderAdapter.OPENAI,
    )
