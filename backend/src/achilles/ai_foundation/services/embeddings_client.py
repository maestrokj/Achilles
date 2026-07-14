"""Client of the built-in embeddings runtime (knowledge-store/embedding-runtime.html).

The runtime is the Platform provider's base_url: an OpenAI-compatible service
that loads weights lazily and pre-warms the assigned model. Calls here are
best-effort by design — an unreachable runtime must not fail an assignment
PATCH, the backend startup or a search: `embed()` answers None and the caller
degrades (ingest leaves embedding NULL, hybrid search runs on lexical/graph/sql).
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.ai_foundation.constants import CODE_MODEL_TOO_LARGE, AiFunction
from achilles.ai_foundation.models import AiModel, AiProvider, ModelAssignment
from achilles.api.problems import ApiError

logger = logging.getLogger(__name__)

_LOAD_TIMEOUT = 30.0  # engine spin-up is async server-side; this is just the HTTP call
# The online path (search) sits on this call — degrade over waiting. Background
# batch callers (ingest, model-change re-embed) pass their own, far larger budget:
# a full batch on a CPU embedder runs tens of seconds and must not read as a
# timeout, which the re-embed loop would misdiagnose as an unready runtime.
_EMBED_TIMEOUT = 10.0


@dataclass(frozen=True, slots=True)
class EmbedResult:
    vectors: list[list[float]]  # aligned with the input texts
    model: AiModel
    prompt_tokens: int | None  # runtime-reported usage; None when absent


async def warm_assigned(session: AsyncSession, model: AiModel) -> None:
    """Warm the built-in runtime after an embedding assignment, best-effort.

    Only the system (Platform) provider is ours to warm — cloud providers
    have no load step. The single home of that rule; callers just hand over
    the newly assigned model.
    """
    provider = await session.get(AiProvider, model.provider_id)
    if provider is not None and provider.is_system and provider.base_url:
        await ensure_loaded(provider.base_url, model.model_id)


async def ensure_loaded(base_url: str, model_id: str) -> bool:
    """Ask the runtime to load + warm a model; returns False when unreachable."""
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=_LOAD_TIMEOUT) as client:
            response = await client.post("/admin/load", json={"model_id": model_id})
            response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("embeddings runtime load skipped (%s): %s", model_id, exc)
        return False
    return True


@dataclass(frozen=True, slots=True)
class RuntimeStatus:
    """Parsed GET /admin/status: which models the runtime holds and how."""

    desired: str | None
    models: dict[str, tuple[str, str | None]]  # model_id -> (state, error)

    def state_of(self, model_id: str) -> str:
        return self.models.get(model_id, ("not_loaded", None))[0]

    def error_of(self, model_id: str) -> str | None:
        return self.models.get(model_id, ("not_loaded", None))[1]


async def runtime_status(base_url: str) -> RuntimeStatus | None:
    """The runtime's per-model state (loading/ready/error); None = unreachable.

    A 404 means an older runtime image without /admin/status — treated as
    unreachable so callers degrade instead of breaking on a rollout skew.
    """
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=5.0) as client:
            response = await client.get("/admin/status")
            response.raise_for_status()
            payload = response.json()
        models = {
            model_id: (str(info.get("state", "not_loaded")), info.get("error"))
            for model_id, info in dict(payload.get("models") or {}).items()
        }
        return RuntimeStatus(desired=payload.get("desired"), models=models)
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        logger.warning("embeddings runtime status unavailable: %s", exc)
        return None


@dataclass(frozen=True, slots=True)
class PreflightResult:
    fits: bool
    required_bytes: int | None
    budget_bytes: int | None


async def preflight(base_url: str, model_id: str) -> PreflightResult | None:
    """Ask the runtime whether `model_id` fits its memory budget; None = no answer."""
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
            response = await client.post("/admin/preflight", json={"model_id": model_id})
            response.raise_for_status()
            payload = response.json()
        return PreflightResult(
            fits=bool(payload["fits"]),
            required_bytes=payload.get("required_bytes"),
            budget_bytes=payload.get("budget_bytes"),
        )
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        logger.warning("embeddings preflight unavailable (%s): %s", model_id, exc)
        return None


async def ensure_model_fits(session: AsyncSession, model_pk: int) -> None:
    """409 MODEL_TOO_LARGE when the built-in runtime says the model can't fit.

    Best-effort by design: only the system (Platform) provider has a runtime to
    ask, and an unreachable runtime must not brick model management — the fit
    is re-checked for real when /admin/load runs. Unknown models fall through
    to the assignment validation, which owns that error.
    """
    model = await session.get(AiModel, model_pk)
    if model is None:
        return
    provider = await session.get(AiProvider, model.provider_id)
    if provider is None or not provider.is_system or not provider.base_url:
        return
    result = await preflight(provider.base_url, model.model_id)
    if result is not None and not result.fits:
        raise ApiError(
            409,
            CODE_MODEL_TOO_LARGE,
            "Model too large",
            f"{model.model_id} needs more memory than the embeddings runtime has "
            f"(required ~{result.required_bytes}, budget {result.budget_bytes} bytes). "
            "Raise the container's memory limit or pick a smaller model.",
        )


async def resolve_assigned(session: AsyncSession) -> tuple[AiModel, AiProvider] | None:
    """The assigned harvester_embedding model with its provider; None → nothing assigned."""
    row = (
        await session.execute(
            sa.select(AiModel, AiProvider)
            .join(ModelAssignment, ModelAssignment.model_id == AiModel.id)
            .join(AiProvider, AiProvider.id == AiModel.provider_id)
            .where(ModelAssignment.function == AiFunction.HARVESTER_EMBEDDING)
        )
    ).first()
    if row is None:
        return None
    model, provider = row
    return model, provider


async def embed(
    session: AsyncSession, texts: Sequence[str], *, http_timeout: float = _EMBED_TIMEOUT
) -> EmbedResult | None:
    """Embed texts with the assigned model; None = soft degradation, never raises.

    The runtime address is the provider's base_url (OpenAI-compatible
    POST /v1/embeddings). A cloud provider without meta.embedding_dim can't
    pass the assignment guard, so in practice this reaches the built-in
    runtime — no auth header needed.

    `http_timeout` defaults to the online-search budget; background batch callers
    override it — a large batch on a CPU embedder legitimately runs far longer
    than a single query, and a false timeout there reads as an unready runtime.
    """
    resolved = await resolve_assigned(session)
    if resolved is None:
        logger.warning("embed skipped: no harvester_embedding assignment")
        return None
    model, provider = resolved
    if not provider.base_url:
        logger.warning("embed skipped: provider %s has no base_url", provider.name)
        return None
    try:
        async with httpx.AsyncClient(base_url=provider.base_url, timeout=http_timeout) as client:
            response = await client.post(
                "/v1/embeddings", json={"model": model.model_id, "input": list(texts)}
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        logger.warning("embeddings runtime unavailable (%s): %s", model.model_id, exc)
        return None
    try:
        data = sorted(payload["data"], key=lambda item: item["index"])
        vectors = [item["embedding"] for item in data]
    except (KeyError, TypeError) as exc:  # alien payload degrades like a silent runtime
        logger.warning("embeddings runtime answered garbage (%s): %s", model.model_id, exc)
        return None
    usage = payload.get("usage") or {}
    return EmbedResult(vectors=vectors, model=model, prompt_tokens=usage.get("prompt_tokens"))
