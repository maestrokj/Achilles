"""The chunker seam: real tokenizer by assignment, whitespace fallback (P1).

No network: the HF download path is respx-mocked with a tiny real
tokenizer.json fixture (WordPiece: "tokens" → 2 subwords, so counts differ
from the whitespace approximation visibly).
"""

from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.ai_foundation.constants import AiFunction
from achilles.ai_foundation.models import ModelAssignment
from achilles.ai_foundation.services import tokenizer
from achilles.knowledge_store.services.chunking import chunk_body
from tests.factories.ai import get_builtin_model

pytestmark = [pytest.mark.integration, pytest.mark.p1]

FIXTURE = Path(__file__).parent.parent / "fixtures" / "tiny_tokenizer.json"


@pytest.fixture(autouse=True)
def clean_tokenizer_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tokenizer, "_CACHE_DIR", tmp_path / "tokenizers")
    tokenizer._counters.clear()  # pyright: ignore[reportPrivateUsage] — test resets module cache


@pytest.fixture
def hf_download(hibp_clean: respx.MockRouter) -> respx.Route:
    return hibp_clean.get(url__startswith="https://huggingface.co/").mock(
        return_value=httpx.Response(200, content=FIXTURE.read_bytes())
    )


async def _assign_builtin(db_session: AsyncSession) -> None:
    builtin = await get_builtin_model(db_session)
    db_session.add(ModelAssignment(function=AiFunction.HARVESTER_EMBEDDING, model_id=builtin.id))
    await db_session.commit()


async def test_no_assignment_means_no_counter(db_session: AsyncSession):
    assert await tokenizer.get_token_counter(db_session) is None


async def test_counter_follows_the_assignment(db_session: AsyncSession, hf_download: respx.Route):
    await _assign_builtin(db_session)
    counter = await tokenizer.get_token_counter(db_session)
    assert counter is not None
    assert hf_download.called
    assert "BAAI/bge-m3" in str(hf_download.calls.last.request.url)
    assert counter("hello world tokens") == 4  # subwords ≠ whitespace's 3


async def test_download_is_cached_across_calls(db_session: AsyncSession, hf_download: respx.Route):
    await _assign_builtin(db_session)
    first = await tokenizer.get_token_counter(db_session)
    second = await tokenizer.get_token_counter(db_session)
    assert first is second
    assert hf_download.call_count == 1  # in-process LRU, not a re-download


async def test_network_failure_falls_back_to_none(
    db_session: AsyncSession, hibp_clean: respx.MockRouter
):
    hibp_clean.get(url__startswith="https://huggingface.co/").mock(
        side_effect=httpx.ConnectError("offline")
    )
    await _assign_builtin(db_session)
    assert await tokenizer.get_token_counter(db_session) is None  # chunker keeps working


async def test_chunker_accepts_injected_counter():
    body = "hello world tokens"
    with_whitespace = chunk_body(body)
    with_real = chunk_body(body, token_counter=lambda text: 2 * len(text.split()))
    assert with_whitespace[0].token_count == 3
    assert with_real[0].token_count == 6
    assert with_whitespace[0].content_hash == with_real[0].content_hash  # text unchanged
