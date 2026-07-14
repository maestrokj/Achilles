"""client_for: adapter → client class, base URL wiring, key decryption (unit).

Wiring is asserted end-to-end: the built client streams against respx and the
request's URL/auth headers prove which base and key it carries.
"""

import pytest
import respx
from httpx import Response

from achilles.ai_foundation.constants import ProviderAdapter
from achilles.ai_foundation.llm import (
    AnthropicChatClient,
    ChatClient,
    ChatMessage,
    OpenAIChatClient,
    client_for,
)
from achilles.ai_foundation.models import AiProvider
from achilles.api.problems import ApiError
from achilles.auth.security.crypto import derive_crypto_key, encrypt
from tests.ai_foundation.unit.llm_wire import anthropic_text_body, collect, openai_text_body

pytestmark = [pytest.mark.unit, pytest.mark.p1]

KEY = derive_crypto_key(crypto_key="", secret_key="llm-factory-tests")


def provider(
    adapter: ProviderAdapter, *, base_url: str | None = None, api_key: str | None = "sk-live"
) -> AiProvider:
    return AiProvider(
        name="p",
        kind="cloud",
        adapter=adapter.value,
        base_url=base_url,
        api_key_enc=encrypt(api_key, key=KEY) if api_key else None,
    )


async def drive(client: ChatClient) -> None:
    await collect(
        client.stream(
            model="m",
            system="s",
            messages=[ChatMessage(role="user", content="q")],
            max_tokens=32,
        )
    )


def mock_openai_dialect(router: respx.MockRouter, url: str) -> respx.Route:
    return router.post(url).mock(
        return_value=Response(
            200, content=openai_text_body("ok"), headers={"content-type": "text/event-stream"}
        )
    )


async def test_anthropic_adapter_default_host_and_key(hibp_clean: respx.MockRouter):
    route = hibp_clean.post("https://api.anthropic.com/v1/messages").mock(
        return_value=Response(
            200, content=anthropic_text_body("ok"), headers={"content-type": "text/event-stream"}
        )
    )
    client = client_for(provider(ProviderAdapter.ANTHROPIC), crypto_key=KEY)
    assert isinstance(client, AnthropicChatClient)
    await drive(client)
    assert route.calls.last.request.headers["x-api-key"] == "sk-live"


async def test_anthropic_adapter_respects_base_url(hibp_clean: respx.MockRouter):
    route = hibp_clean.post("https://claude.corp.test/v1/messages").mock(
        return_value=Response(
            200, content=anthropic_text_body("ok"), headers={"content-type": "text/event-stream"}
        )
    )
    client = client_for(
        provider(ProviderAdapter.ANTHROPIC, base_url="https://claude.corp.test"), crypto_key=KEY
    )
    await drive(client)
    assert route.called


async def test_openai_adapter_default_host_and_bearer(hibp_clean: respx.MockRouter):
    route = mock_openai_dialect(hibp_clean, "https://api.openai.com/v1/chat/completions")
    client = client_for(provider(ProviderAdapter.OPENAI), crypto_key=KEY)
    assert isinstance(client, OpenAIChatClient)
    await drive(client)
    assert route.calls.last.request.headers["authorization"] == "Bearer sk-live"


async def test_google_adapter_uses_openai_compatible_endpoint(hibp_clean: respx.MockRouter):
    url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    route = mock_openai_dialect(hibp_clean, url)
    client = client_for(provider(ProviderAdapter.GOOGLE), crypto_key=KEY)
    assert isinstance(client, OpenAIChatClient)
    await drive(client)
    assert route.called


async def test_ollama_adapter_is_keyless_openai_dialect(hibp_clean: respx.MockRouter):
    route = mock_openai_dialect(hibp_clean, "http://ollama:11434/v1/chat/completions")
    client = client_for(
        provider(ProviderAdapter.OLLAMA, base_url="http://ollama:11434", api_key=None),
        crypto_key=KEY,
    )
    assert isinstance(client, OpenAIChatClient)
    await drive(client)
    # The SDK refuses an empty key, so keyless providers get a placeholder.
    assert route.calls.last.request.headers["authorization"].startswith("Bearer ")


async def test_openai_compatible_uses_stored_base_url(hibp_clean: respx.MockRouter):
    route = mock_openai_dialect(hibp_clean, "https://vllm.corp.test/v1/chat/completions")
    client = client_for(
        provider(ProviderAdapter.OPENAI_COMPATIBLE, base_url="https://vllm.corp.test/"),
        crypto_key=KEY,
    )
    await drive(client)
    assert route.called


def test_openai_compatible_without_base_url_is_unreachable():
    with pytest.raises(ApiError) as excinfo:
        client_for(provider(ProviderAdapter.OPENAI_COMPATIBLE), crypto_key=KEY)
    assert excinfo.value.status == 502
    assert excinfo.value.code == "PROVIDER_UNREACHABLE"


def test_undecryptable_key_is_unreachable_not_500():
    other_key = derive_crypto_key(crypto_key="", secret_key="rotated-away")
    row = provider(ProviderAdapter.OPENAI)
    with pytest.raises(ApiError) as excinfo:
        client_for(row, crypto_key=other_key)
    assert excinfo.value.status == 502
    assert excinfo.value.code == "PROVIDER_UNREACHABLE"
