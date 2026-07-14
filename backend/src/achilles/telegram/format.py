"""Minimal markdown → Telegram HTML: bold and links. Completeness is a non-goal (v1).

Telegram's HTML parse_mode is safer than MarkdownV2 — only `< > &` need escaping,
and the markdown markers (`**`, `[](…)`) are not among them, so we escape first
and then translate the two constructs we emit.
"""

import html
import re

from achilles.query_engine.schemas import CitationOut

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def to_html(text: str) -> str:
    text = html.escape(text, quote=False)
    text = _BOLD.sub(r"<b>\1</b>", text)
    return _LINK.sub(_link_anchor, text)


def _link_anchor(match: re.Match[str]) -> str:
    # The text was already html-escaped (quote=False), so < > & are handled; only
    # the attribute-breaking " survives and must be neutralized inside the href,
    # or Telegram rejects the whole message and the answer is silently dropped.
    label, url = match.group(1), match.group(2)
    href = url.replace('"', "&quot;")
    return f'<a href="{href}">{label}</a>'


def sources_block(citations: list[CitationOut], *, heading: str) -> str:
    """The record-level citation list appended under the answer."""
    lines = [html.escape(heading, quote=False)]
    for citation in citations:
        title = html.escape(citation.title or citation.source_type, quote=False)
        entry = (
            f'<a href="{html.escape(citation.url, quote=True)}">{title}</a>'
            if citation.url
            else title
        )
        lines.append(f"[{citation.marker}] {entry}")
    return "\n".join(lines)
