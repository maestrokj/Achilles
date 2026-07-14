"""Telegram rendering of the shared messenger phrases: HTML parse_mode markup.

One platform-only extra: the `/new` command confirmation (no threads on this
surface, so the conversation is cut by hand — telegram/index.html#conversation).
"""

from achilles.messenger.phrases import PhraseStyle, build_phrases, make_phrase_fn

PHRASES = build_phrases(
    PhraseStyle(
        platform="Telegram",
        link=lambda url, label: f'<a href="{url}">{label}</a>',
        italic=lambda text: f"<i>{text}</i>",
        extra={
            "en": {"new_conversation": "Started a new conversation. Ask away."},
            "ru": {"new_conversation": "Начат новый диалог. Задавайте вопрос."},
        },
    )
)

phrase = make_phrase_fn(PHRASES)
