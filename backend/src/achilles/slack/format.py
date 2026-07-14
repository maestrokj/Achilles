"""Minimal markdown → Slack mrkdwn: bold and links. Completeness is a non-goal (v1)."""

import re

from achilles.query_engine.schemas import CitationOut

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def to_mrkdwn(text: str) -> str:
    text = _BOLD.sub(r"*\1*", text)
    return _LINK.sub(r"<\2|\1>", text)


def sources_block(citations: list[CitationOut], *, heading: str) -> str:
    """The record-level citation list appended under the answer."""
    lines = [heading]
    for citation in citations:
        title = citation.title or citation.source_type
        entry = f"<{citation.url}|{title}>" if citation.url else title
        lines.append(f"[{citation.marker}] {entry}")
    return "\n".join(lines)
