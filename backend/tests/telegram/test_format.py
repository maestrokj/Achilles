"""markdown → Telegram HTML conversion and the sources block (unit)."""

import pytest

from achilles.query_engine.schemas import CitationOut
from achilles.telegram.format import sources_block, to_html

pytestmark = [pytest.mark.unit, pytest.mark.p1]


def test_bold_and_links_convert():
    assert (
        to_html("**bold** and [doc](https://x.test/a)")
        == '<b>bold</b> and <a href="https://x.test/a">doc</a>'
    )


def test_html_special_chars_are_escaped():
    # `<`, `>` and `&` must not reach Telegram as raw HTML.
    assert to_html("a < b && c > d") == "a &lt; b &amp;&amp; c &gt; d"


def test_plain_text_untouched():
    assert to_html("plain text") == "plain text"


def test_link_url_with_quote_is_escaped():
    # A double-quote in the URL must not break the href attribute — otherwise
    # Telegram rejects the whole message and the answer is silently dropped.
    assert to_html('[x](https://h.test/a"b)') == '<a href="https://h.test/a&quot;b">x</a>'


def test_sources_block_renders_markers_and_links():
    citations = [
        CitationOut(marker=1, entity_id=1, source_type="page", title="Doc", url="https://x.test"),
        CitationOut(marker=2, entity_id=2, source_type="ticket", title=None, url=None),
    ]
    block = sources_block(citations, heading="Sources:")
    assert block == 'Sources:\n[1] <a href="https://x.test">Doc</a>\n[2] ticket'
