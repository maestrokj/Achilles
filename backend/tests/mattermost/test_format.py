"""Markdown passthrough and the sources block (unit)."""

import pytest

from achilles.mattermost.format import sources_block, to_markdown
from achilles.query_engine.schemas import CitationOut

pytestmark = [pytest.mark.unit, pytest.mark.p1]


def test_markdown_passes_through_untouched():
    text = "**bold**, [doc](https://x.test/a) and `code`"
    assert to_markdown(text) == text


def test_sources_block_renders_markers_and_links():
    citations = [
        CitationOut(marker=1, entity_id=1, source_type="page", title="Doc", url="https://x.test"),
        CitationOut(marker=2, entity_id=2, source_type="ticket", title=None, url=None),
    ]
    block = sources_block(citations, heading="Sources:")
    assert block == "Sources:\n[1] [Doc](https://x.test)\n[2] ticket"
