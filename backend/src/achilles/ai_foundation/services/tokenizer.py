"""Real token counting for the chunker (closes the KS→AI Foundation seam).

The assigned harvester_embedding model decides the tokenizer: a *builtin*
model's model_id is the HF repo, whose tokenizer.json downloads lazily into
a local cache and loads once per process (tokenizers' Rust core, no torch
involved). Every failure mode — no assignment, a non-builtin model whose id
is no HF repo, no network — answers None and the chunker keeps its
whitespace approximation: counting tokens worse is acceptable, failing
ingest over it is not.
"""

import asyncio
import logging
import os
from collections.abc import Callable
from pathlib import Path

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from tokenizers import Tokenizer

from achilles.ai_foundation.constants import AiFunction, ModelOrigin
from achilles.ai_foundation.models import AiModel, ModelAssignment

logger = logging.getLogger(__name__)

TokenCounter = Callable[[str], int]

APPROX_CHARS_PER_TOKEN = 4


def approx_counter(text: str) -> int:
    """The honest fallback when no builtin tokenizer is available."""
    return max(1, len(text) // APPROX_CHARS_PER_TOKEN)


_DOWNLOAD_TIMEOUT = 30.0
_CACHE_DIR = Path.home() / ".cache" / "achilles" / "tokenizers"

# Per-process cache: model_id → counter (or None after a failed attempt, so a
# broken repo is not re-downloaded on every entity). Guarded by _resolve_lock:
# concurrent ingest tasks must not race the download/parse or the None-caching.
_counters: dict[str, TokenCounter | None] = {}
_resolve_lock = asyncio.Lock()


def _tokenizer_url(model_id: str) -> str:
    return f"https://huggingface.co/{model_id}/resolve/main/tokenizer.json"


async def _fetch_tokenizer_file(model_id: str) -> Path | None:
    path = _CACHE_DIR / model_id.replace("/", "--") / "tokenizer.json"
    if path.is_file():
        return path
    try:
        async with httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(_tokenizer_url(model_id))
            response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("tokenizer for %s unavailable: %s", model_id, exc)
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    # Off the loop (multi-MB file on the SAQ ingest path) and atomic: write a
    # temp file, then os.replace so a concurrent process never sees a partial
    # tokenizer.json through is_file().
    await asyncio.to_thread(_write_atomic, path, response.content)
    return path


def _write_atomic(path: Path, content: bytes) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(content)
    tmp.replace(path)


async def counter_for_model(model_id: str) -> TokenCounter | None:
    if model_id in _counters:
        return _counters[model_id]
    async with _resolve_lock:
        if model_id in _counters:  # another task resolved it while we waited
            return _counters[model_id]
        counter = await _build_counter(model_id)
        _counters[model_id] = counter
        return counter


async def _build_counter(model_id: str) -> TokenCounter | None:
    path = await _fetch_tokenizer_file(model_id)
    if path is None:
        return None
    try:
        # Rust-side parse of a multi-MB file — keep it off the event loop.
        tokenizer = await asyncio.to_thread(Tokenizer.from_file, str(path))
    except Exception:  # a corrupt/alien file must degrade, not break ingest
        logger.exception("tokenizer file for %s failed to load", model_id)
        return None

    def _count(text: str) -> int:
        return len(tokenizer.encode(text).ids)

    return _count


async def assigned_builtin_model_id(session: AsyncSession) -> str | None:
    """HF repo of the assigned embedding model, or None.

    Only builtin models qualify: their model_id is an HF repo by contract.
    Other origins carry provider-specific ids — probing HF with those would
    just cache a 404 per process.
    """
    return (
        await session.execute(
            sa.select(AiModel.model_id)
            .join(ModelAssignment, ModelAssignment.model_id == AiModel.id)
            .where(
                ModelAssignment.function == AiFunction.HARVESTER_EMBEDDING,
                AiModel.origin == ModelOrigin.BUILTIN,
            )
        )
    ).scalar_one_or_none()


async def get_token_counter(session: AsyncSession) -> TokenCounter | None:
    """Counter of the assigned embedding model; None → caller falls back."""
    model_id = await assigned_builtin_model_id(session)
    if model_id is None:
        return None
    return await counter_for_model(model_id)
