"""markdown → mrkdwn conversion and the sources block (unit)."""

import pytest

from achilles.query_engine.schemas import CitationOut
from achilles.slack.format import sources_block, to_mrkdwn

pytestmark = [pytest.mark.unit, pytest.mark.p1]


def test_bold_and_links_convert():
    assert to_mrkdwn("**bold** and [doc](https://x.test/a)") == "*bold* and <https://x.test/a|doc>"


def test_plain_text_untouched():
    assert to_mrkdwn("plain *slack bold* text") == "plain *slack bold* text"


def test_sources_block_renders_markers_and_links():
    citations = [
        CitationOut(marker=1, entity_id=1, source_type="page", title="Doc", url="https://x.test"),
        CitationOut(marker=2, entity_id=2, source_type="ticket", title=None, url=None),
    ]
    block = sources_block(citations, heading="Sources:")
    assert block == "Sources:\n[1] <https://x.test|Doc>\n[2] ticket"
