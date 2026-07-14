"""Provider discovery: list models by adapter dialect (data-model.html#discovery).

Configuration reads, not inference — plain httpx against each dialect's
catalog endpoint. An unreachable upstream is the caller's news (502
PROVIDER_UNREACHABLE with the reason), never a 500.
"""

import json
from typing import Any

import httpx

from achilles.ai_foundation.constants import (
    CODE_PROVIDER_UNREACHABLE,
    ModelType,
    ProviderAdapter,
)
from achilles.ai_foundation.models import AiProvider
from achilles.ai_foundation.schemas import DiscoveredModel
from achilles.api.problems import ApiError
from achilles.auth.security.crypto import CiphertextInvalidError, decrypt

_TIMEOUT = 10.0
_ANTHROPIC_VERSION = "2023-06-01"

# Shared with llm.factory — one place decides where an adapter lives by default.
DEFAULT_HOSTS = {
    ProviderAdapter.OPENAI: "https://api.openai.com",
    ProviderAdapter.ANTHROPIC: "https://api.anthropic.com",
    ProviderAdapter.GOOGLE: "https://generativelanguage.googleapis.com",
}


# Shared with llm.factory, like DEFAULT_HOSTS: any talk to a provider fails
# the same way — 502 PROVIDER_UNREACHABLE, never a 500.
def unreachable(reason: str) -> ApiError:
    return ApiError(502, CODE_PROVIDER_UNREACHABLE, "Provider unreachable", reason)


def resolve_base_url(provider: AiProvider) -> str:
    base = provider.base_url or DEFAULT_HOSTS.get(ProviderAdapter(provider.adapter))
    if base is None:
        raise unreachable("the provider has no base URL configured")
    return base.rstrip("/")


def decrypted_api_key(provider: AiProvider, *, key: bytes) -> str | None:
    """The stored key, or None when none is stored; undecryptable → 502.

    A key that can't be decrypted (data key rotated / corrupted) means no
    call to this provider can authenticate — an error status, not a 500.
    """
    try:
        return decrypt(provider.api_key_enc, key=key) if provider.api_key_enc else None
    except CiphertextInvalidError as exc:
        raise unreachable("the stored API key could not be read — save it again") from exc


async def discover_models(provider: AiProvider, *, key: bytes) -> list[DiscoveredModel]:
    api_key = decrypted_api_key(provider, key=key)
    adapter = ProviderAdapter(provider.adapter)
    url, headers, params = _catalog_request(adapter, resolve_base_url(provider), api_key)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(url, headers=headers, params=params)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as exc:
        raise unreachable(
            f"the provider returned HTTP {exc.response.status_code} for {url}"
        ) from exc
    except httpx.HTTPError as exc:
        raise unreachable(f"could not connect to {url}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise unreachable(f"the provider at {url} returned an unexpected response") from exc
    return _parse_catalog(adapter, payload)


def _catalog_request(
    adapter: ProviderAdapter, base: str, api_key: str | None
) -> tuple[str, dict[str, str], dict[str, str]]:
    headers: dict[str, str] = {}
    params: dict[str, str] = {}
    match adapter:
        case ProviderAdapter.OPENAI | ProviderAdapter.OPENAI_COMPATIBLE:
            url = f"{base}/v1/models"
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
        case ProviderAdapter.ANTHROPIC:
            url = f"{base}/v1/models"
            headers["anthropic-version"] = _ANTHROPIC_VERSION
            if api_key:
                headers["x-api-key"] = api_key
        case ProviderAdapter.GOOGLE:
            url = f"{base}/v1beta/models"
            if api_key:
                params["key"] = api_key
        case ProviderAdapter.OLLAMA:
            url = f"{base}/api/tags"
    return url, headers, params


def _type_from_id(model_id: str) -> ModelType:
    """Infer a model's type from its id — an OpenAI-style catalog carries none.

    Providers name embedders with 'embed'/'embedding', everything else defaults to
    chat. A good default, not an oracle — the admin flips it inline (the catalogue's
    type select).
    """
    return ModelType.EMBEDDING if "embed" in model_id.lower() else ModelType.CHAT


def _type_from_google_methods(methods: list[str]) -> ModelType:
    """Read the model's type from Gemini's authoritative catalog.

    A model advertising embedContent is an embedder; otherwise it serves
    generateContent as a chat model.
    """
    return ModelType.EMBEDDING if "embedContent" in methods else ModelType.CHAT


def _parse_catalog(adapter: ProviderAdapter, payload: dict[str, Any]) -> list[DiscoveredModel]:
    try:
        match adapter:
            case ProviderAdapter.OPENAI | ProviderAdapter.OPENAI_COMPATIBLE:
                return [
                    DiscoveredModel(
                        model_id=item["id"],
                        display_name=item.get("display_name"),
                        model_type=_type_from_id(item["id"]),
                    )
                    for item in payload["data"]
                ]
            case ProviderAdapter.ANTHROPIC:
                # Anthropic serves no embedding models — every entry is chat.
                return [
                    DiscoveredModel(
                        model_id=item["id"],
                        display_name=item.get("display_name"),
                        model_type=ModelType.CHAT,
                    )
                    for item in payload["data"]
                ]
            case ProviderAdapter.GOOGLE:
                return [
                    DiscoveredModel(
                        model_id=item["name"].removeprefix("models/"),
                        display_name=item.get("displayName"),
                        model_type=_type_from_google_methods(
                            item.get("supportedGenerationMethods", [])
                        ),
                    )
                    for item in payload["models"]
                ]
            case ProviderAdapter.OLLAMA:
                return [
                    DiscoveredModel(model_id=item["name"], model_type=_type_from_id(item["name"]))
                    for item in payload["models"]
                ]
    except (KeyError, TypeError) as exc:
        raise unreachable(
            f"the {adapter} provider returned an unexpected model list format"
        ) from exc
