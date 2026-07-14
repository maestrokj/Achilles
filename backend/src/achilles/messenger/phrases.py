"""The bot's own service phrases (not model output), built once per platform.

The dialogue itself speaks whatever language the person writes in — these are
the few fixed lines around linking and failures. The copy is identical across
platforms; a style contributes only the platform name and its markup for links
and italics, plus any platform-specific extras (Telegram's `/new`). Unknown
locale → English.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

_DEFAULT = "en"

type PhraseFn = Callable[..., str]


@dataclass(frozen=True, slots=True)
class PhraseStyle:
    """What a platform contributes to the shared phrase tables."""

    platform: str  # display name, used verbatim in both locales
    link: Callable[[str, str], str]  # (url, label) -> platform link markup
    italic: Callable[[str], str]
    extra: Mapping[str, Mapping[str, str]] = field(default_factory=dict)  # locale -> extras


def build_phrases(style: PhraseStyle) -> dict[str, dict[str, str]]:
    """One canonical copy per locale; the style renders the platform-specific bits.

    Link markup is built around the literal `{link_url}` placeholder so the
    resulting strings keep formatting lazily via `phrase()`, exactly like the
    hand-written tables they replace.
    """
    name = style.platform
    tables: dict[str, dict[str, str]] = {
        "en": {
            "linked": (
                f"This {name} account is now linked to {{email}}. You can ask your questions here."
            ),
            "not_linked": (
                f"This {name} account is not linked yet. Sign in to the web app and "
                f"{style.link('{link_url}', 'open the link page')}, "
                "then send the one-time code here."
            ),
            "link_expired": (
                "This code has expired or was already used. Request a new one in the web app."
            ),
            "already_linked": f"This {name} account is already linked.",
            "too_many_attempts": "Too many incorrect codes. Try again later.",
            "access_revoked": "Your account is no longer active. Contact an administrator.",
            "turn_failed": "Could not get an answer ({code}). Please try again.",
            "turn_cut_off": style.italic("(interrupted — please try again)"),
            "sources": "Sources:",
        },
        "ru": {
            "linked": f"Этот {name}-аккаунт привязан к {{email}}. Можете задавать вопросы прямо здесь.",  # noqa: E501
            "not_linked": (
                f"Этот {name}-аккаунт ещё не привязан. Войдите в веб-приложение и "
                f"{style.link('{link_url}', 'откройте страницу привязки')}, "
                "затем отправьте сюда одноразовый код."
            ),
            "link_expired": "Код истёк или уже использован. Запросите новый в веб-приложении.",
            "already_linked": f"Этот {name}-аккаунт уже привязан.",
            "too_many_attempts": "Слишком много неверных кодов. Попробуйте позже.",
            "access_revoked": "Ваша учётная запись больше не активна. Обратитесь к администратору.",
            "turn_failed": "Не удалось получить ответ ({code}). Попробуйте ещё раз.",  # noqa: RUF001 — Cyrillic copy, the {code} placeholder confuses the confusables check
            "turn_cut_off": style.italic("(прервано — попробуйте ещё раз)"),
            "sources": "Источники:",
        },
    }
    for locale, extras in style.extra.items():
        tables[locale].update(extras)
    return tables


def make_phrase_fn(phrases: Mapping[str, Mapping[str, str]]) -> PhraseFn:
    def phrase(locale: str, key: str, **kwargs: str) -> str:
        table = phrases.get(locale, phrases[_DEFAULT])
        return table[key].format(**kwargs)

    return phrase
