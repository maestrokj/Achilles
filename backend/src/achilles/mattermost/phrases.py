"""Mattermost rendering of the shared messenger phrases: standard Markdown."""

from achilles.messenger.phrases import PhraseStyle, build_phrases, make_phrase_fn

PHRASES = build_phrases(
    PhraseStyle(
        platform="Mattermost",
        link=lambda url, label: f"[{label}]({url})",
        italic=lambda text: f"_{text}_",
    )
)

phrase = make_phrase_fn(PHRASES)
