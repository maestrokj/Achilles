"""Runtime language directive: match the request, name the detected language,
fall back to the user locale when the message can't tell."""

from types import SimpleNamespace
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.ai_foundation.services import prompt

pytestmark = [pytest.mark.unit]

SESSION = cast("AsyncSession", object())


def _stub_org_locale(monkeypatch: pytest.MonkeyPatch, locale: str) -> None:
    async def fake_get_platform_settings(session: AsyncSession) -> SimpleNamespace:
        del session
        return SimpleNamespace(locale=locale)

    monkeypatch.setattr(prompt.platform, "get_platform_settings", fake_get_platform_settings)


async def test_no_user_locale_falls_back_to_org(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_org_locale(monkeypatch, "ru")
    text = await prompt.language_policy(SESSION, user_locale=None)
    assert text.startswith("Отвечай на языке запроса")
    assert "на русском" in text  # org locale names itself


async def test_user_locale_overrides_the_fallback_target(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_org_locale(monkeypatch, "ru")
    text = await prompt.language_policy(SESSION, user_locale="en")
    assert "на английском" in text  # directive stays in the prompt locale (ru)


async def test_directive_is_written_in_the_prompt_locale(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_org_locale(monkeypatch, "en")
    text = await prompt.language_policy(SESSION, user_locale="ru")
    assert text.startswith("Answer in the language")
    assert "in Russian" in text


async def test_unknown_user_locale_degrades_to_the_prompt_locale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_org_locale(monkeypatch, "en")
    text = await prompt.language_policy(SESSION, user_locale="zz")
    assert "in English" in text


async def test_detected_english_message_is_named_outright(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_org_locale(monkeypatch, "en")
    text = await prompt.language_policy(
        SESSION,
        user_locale="ru",
        message_text="How does our ETA calculation work, from the model to caching?",
    )
    assert "current message is written in English" in text
    assert "ambiguous" not in text  # the concrete sentence replaces the fallback


async def test_detected_russian_message_wins_over_the_locale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_org_locale(monkeypatch, "en")
    text = await prompt.language_policy(
        SESSION, user_locale="en", message_text="Как работает ETA caching в eta-svc?"
    )
    assert "current message is written in Russian" in text


async def test_ambiguous_message_keeps_the_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_org_locale(monkeypatch, "ru")
    text = await prompt.language_policy(SESSION, user_locale="en", message_text="ok")
    assert "Если запрос слишком короткий" in text
    assert "на английском" in text


def test_detect_english_prose() -> None:
    assert prompt.detect_language("What is the status of the deploy?") == "en"


def test_detect_russian_with_latin_tech_terms() -> None:
    assert prompt.detect_language("Как задеплоить eta-svc через docker compose?") == "ru"


def test_bare_noun_phrase_is_not_confident() -> None:
    assert prompt.detect_language("deploy status") is None


def test_short_or_empty_is_not_confident() -> None:
    assert prompt.detect_language("ok") is None
    assert prompt.detect_language("да") is None
    assert prompt.detect_language("") is None
