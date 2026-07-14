"""Mattermost speaks standard Markdown — the model's output passes through as-is.

The function stays so the surface profile has the same shape as its twins and a
future dialect tweak has a home.
"""

from achilles.query_engine.schemas import CitationOut


def to_markdown(text: str) -> str:
    return text


def sources_block(citations: list[CitationOut], *, heading: str) -> str:
    """The record-level citation list appended under the answer."""
    lines = [heading]
    for citation in citations:
        title = citation.title or citation.source_type
        entry = f"[{title}]({citation.url})" if citation.url else title
        lines.append(f"[{citation.marker}] {entry}")
    return "\n".join(lines)
