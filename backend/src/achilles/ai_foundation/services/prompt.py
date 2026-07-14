"""The platform prompt layer: effective text, override, reset (prompt-library.html).

NULL column → built-in default for the platform locale; non-NULL → the
admin's frozen override. GET always answers with the effective text plus an
is_default flag; PATCH with null resets. Placeholders are a closed whitelist
({org_name}, {today}) — an unknown {token} dies on save, not at composition.
"""

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Final

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.ai_foundation.constants import (
    CODE_UNKNOWN_PLACEHOLDER,
    PROMPT_MAX_CHARS,
    PROMPT_PLACEHOLDERS,
)
from achilles.ai_foundation.models import PromptSettings
from achilles.ai_foundation.prompt_texts import DEFAULT_PROMPTS
from achilles.ai_foundation.schemas import PromptBlockOut, PromptOut, PromptPatch
from achilles.api.problems import CODE_VALIDATION_ERROR, ApiError
from achilles.knowledge_store.models import PlatformSettings
from achilles.knowledge_store.services import platform

_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")


async def get_settings(session: AsyncSession) -> PromptSettings:
    return (await session.execute(sa.select(PromptSettings))).scalar_one()


def _block(override: str | None, kind: str, locale: str) -> PromptBlockOut:
    default = DEFAULT_PROMPTS[locale][kind]
    return PromptBlockOut(text=override or default, is_default=override is None)


async def get_effective(
    session: AsyncSession, *, settings_row: PlatformSettings | None = None
) -> PromptOut:
    # The org locale (platform_settings.locale) picks the built-in default text;
    # a caller that already holds the singleton passes it to save the re-fetch.
    if settings_row is None:
        settings_row = await platform.get_platform_settings(session)
    locale = settings_row.locale
    row = await get_settings(session)
    return PromptOut(
        safety=_block(row.safety_text, "safety", locale), org=_block(row.org_text, "org", locale)
    )


def render(text: str, values: Mapping[str, str]) -> str:
    """Substitute placeholder values into effective text.

    Lives next to the PROMPT_PLACEHOLDERS whitelist that validate_text
    enforces on save — the composer passes values, one module owns the
    mechanism. Keys must come from that whitelist.
    """
    for token, value in values.items():
        text = text.replace("{" + token + "}", value)
    return text


async def rendered_platform(session: AsyncSession) -> str:
    """The rendered platform layer (safety + org) every AI surface starts from.

    Chat and agents compose their surface-specific layers below this one; the
    block order and the variable vocabulary live here, with their owner.
    {org_name} comes from platform_settings — the same row that picks the locale.
    """
    settings_row = await platform.get_platform_settings(session)
    effective = await get_effective(session, settings_row=settings_row)
    return render(
        effective.safety.text + "\n\n" + effective.org.text,
        {"org_name": settings_row.org_name, "today": datetime.now(UTC).date().isoformat()},
    )


# The runtime language policy for a live user turn (chat + messengers). The
# primary rule is to match the request; when the current message's language is
# detectable, the directive names it outright — weak models hold a concrete
# instruction where they lose a conditional one amid mixed-language evidence.
# The fallback names the user's resolved locale for a message too short or
# ambiguous to tell (a bare log, "ok", one name). Written in the prompt's own
# locale, naming the target language in that same locale. Agents don't use
# this — their output language follows the owner's instructions (agent_engine
# AGENT_FRAME).
_LANGUAGE_NAMES: Final[dict[str, dict[str, str]]] = {
    "ru": {"ru": "русском", "en": "английском"},
    "en": {"ru": "Russian", "en": "English"},
}

_LANGUAGE_RULE: Final[dict[str, str]] = {
    "ru": "Отвечай на языке запроса пользователя.",
    "en": "Answer in the language of the user's request.",
}

_LANGUAGE_DETECTED: Final[dict[str, str]] = {
    "ru": " Текущее сообщение пользователя написано на {lang} — отвечай на {lang}.",
    "en": " The user's current message is written in {lang} — answer in {lang}.",
}

_LANGUAGE_FALLBACK: Final[dict[str, str]] = {
    "ru": " Если запрос слишком короткий или язык неоднозначен — отвечай на {lang}.",
    "en": " If the request is too short or its language is ambiguous, answer in {lang}.",
}

# Words that mark running English prose; bare noun phrases ("deploy status")
# stay undetected on purpose — the fallback locale answers those.
_EN_STOPWORDS: Final[frozenset[str]] = frozenset(
    [
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "do",
        "does",
        "did",
        "how",
        "what",
        "why",
        "when",
        "where",
        "which",
        "who",
        "of",
        "to",
        "in",
        "on",
        "for",
        "and",
        "or",
        "with",
        "from",
        "our",
        "your",
        "their",
        "we",
        "you",
        "it",
        "this",
        "that",
        "can",
        "could",
        "should",
        "would",
        "will",
        "not",
        "be",
        "have",
        "has",
        "had",
    ]
)
_CYRILLIC = re.compile(r"[а-яё]", re.IGNORECASE)  # noqa: RUF001 — the Cyrillic range is the point
_LATIN = re.compile(r"[a-z]", re.IGNORECASE)
_WORD = re.compile(r"[a-z]+", re.IGNORECASE)

# A Russian message keeps its Latin tech terms ("как работает ETA caching?"),
# so Cyrillic wins well below parity; six letters ≈ one real word.
_RU_MIN_LETTERS = 6
_RU_MIN_SHARE = 0.3
_EN_MIN_STOPWORDS = 2


def detect_language(text: str) -> str | None:
    """The current message's language when the script tells it: 'ru' / 'en' / None.

    Cyrillic presence is decisive for Russian; Latin script alone is any
    European language, so English needs running prose — stopwords — not just
    letters. None means "not confident": the caller falls back to the locale.
    """
    cyrillic = len(_CYRILLIC.findall(text))
    latin = len(_LATIN.findall(text))
    letters = cyrillic + latin
    if cyrillic >= _RU_MIN_LETTERS and cyrillic / letters >= _RU_MIN_SHARE:
        return "ru"
    if cyrillic == 0:
        words = (word.lower() for word in _WORD.findall(text))
        if sum(word in _EN_STOPWORDS for word in words) >= _EN_MIN_STOPWORDS:
            return "en"
    return None


async def language_policy(
    session: AsyncSession, *, user_locale: str | None, message_text: str | None = None
) -> str:
    """Runtime language directive for a live user turn (chat, messengers).

    The prompt locale (platform_settings.locale) writes the directive. A
    detectable message language is named outright; otherwise the fallback
    target is the user's resolved locale — their own setting, or the org
    locale when unset (external messenger users) — named in the prompt's
    language.
    """
    settings_row = await platform.get_platform_settings(session)
    prompt_locale = settings_row.locale
    names = _LANGUAGE_NAMES[prompt_locale]
    detected = detect_language(message_text) if message_text else None
    if detected is not None:
        tail = _LANGUAGE_DETECTED[prompt_locale].format(lang=names[detected])
    else:
        fallback = names.get(user_locale or prompt_locale, names[prompt_locale])
        tail = _LANGUAGE_FALLBACK[prompt_locale].format(lang=fallback)
    return _LANGUAGE_RULE[prompt_locale] + tail


def _override_or_reset(text: str | None, *, field: str) -> str | None:
    """Reset to the built-in default, or freeze a trimmed override.

    None, "" and whitespace-only all reset; any real text is trimmed and
    validated before it stores as an override.
    """
    trimmed = (text or "").strip()
    return validate_text(trimmed, field=field) if trimmed else None


def validate_text(text: str, *, field: str) -> str:
    if len(text) > PROMPT_MAX_CHARS:
        raise ApiError(
            422,
            CODE_VALIDATION_ERROR,
            "Prompt too long",
            f"{field} exceeds {PROMPT_MAX_CHARS} characters",
        )
    for token in _PLACEHOLDER.findall(text):
        if token not in PROMPT_PLACEHOLDERS:
            raise ApiError(
                422,
                CODE_UNKNOWN_PLACEHOLDER,
                "Unknown placeholder",
                f"{{{token}}} is not a supported placeholder in {field}",
            )
    return text


async def apply_patch(session: AsyncSession, patch: PromptPatch, *, actor_id: int) -> PromptOut:
    row = await get_settings(session)
    fields = patch.model_fields_set
    if "safety_text" in fields:
        row.safety_text = _override_or_reset(patch.safety_text, field="safety_text")
    if "org_text" in fields:
        row.org_text = _override_or_reset(patch.org_text, field="org_text")
    if fields:
        row.updated_by = actor_id
    await session.commit()
    return await get_effective(session)
