"""Slack rendering of the shared messenger phrases: mrkdwn links, underscore italics."""

from achilles.messenger.phrases import PhraseStyle, build_phrases, make_phrase_fn

PHRASES = build_phrases(
    PhraseStyle(
        platform="Slack",
        link=lambda url, label: f"<{url}|{label}>",
        italic=lambda text: f"_{text}_",
    )
)

phrase = make_phrase_fn(PHRASES)
