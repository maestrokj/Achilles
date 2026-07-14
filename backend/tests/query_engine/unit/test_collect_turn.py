"""collect_turn folds the typed event stream into one message (unit)."""

from collections.abc import AsyncGenerator
from typing import cast

import pytest
from pydantic import BaseModel

from achilles.query_engine import service
from achilles.query_engine.schemas import (
    CitationOut,
    CitationsEvent,
    DeltaEvent,
    DoneEvent,
    ErrorEvent,
)

pytestmark = [pytest.mark.unit, pytest.mark.p1]

CITATION = CitationOut(marker=1, entity_id=7, source_type="page", title="Doc")


def _events(*pairs: tuple[str, BaseModel]):
    async def generator(_context: object) -> AsyncGenerator[tuple[str, BaseModel]]:
        for pair in pairs:
            yield pair

    return generator


async def test_folds_deltas_and_citations(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        service,
        "turn_events",
        _events(
            ("delta", DeltaEvent(text="Hel")),
            ("delta", DeltaEvent(text="lo")),
            ("citations", CitationsEvent(items=[CITATION])),
            ("done", DoneEvent(assistant_message_id=1, tokens_used=15)),
        ),
    )
    collected = await service.collect_turn(cast("service.TurnContext", None))
    assert collected.text == "Hello"
    assert collected.citations == [CITATION]
    assert collected.error_code is None


async def test_error_frame_is_surfaced_with_partial_text(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        service,
        "turn_events",
        _events(
            ("delta", DeltaEvent(text="Half")),
            ("error", ErrorEvent(code="PROVIDER_UNAVAILABLE", detail="down")),
        ),
    )
    collected = await service.collect_turn(cast("service.TurnContext", None))
    assert collected.text == "Half"
    assert collected.error_code == "PROVIDER_UNAVAILABLE"
