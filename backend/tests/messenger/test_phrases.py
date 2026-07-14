"""The shared phrase factory: one copy, platform-rendered markup (unit)."""

import pytest

from achilles.messenger.phrases import PhraseStyle, build_phrases, make_phrase_fn
from achilles.slack.phrases import PHRASES as SLACK_PHRASES
from achilles.telegram.phrases import PHRASES as TELEGRAM_PHRASES

pytestmark = [pytest.mark.unit, pytest.mark.p1]

PHRASE_KEYS = {
    "linked",
    "not_linked",
    "link_expired",
    "already_linked",
    "too_many_attempts",
    "access_revoked",
    "turn_failed",
    "turn_cut_off",
    "sources",
}


def test_slack_tables_render_mrkdwn():
    assert set(SLACK_PHRASES) == {"en", "ru"}
    assert set(SLACK_PHRASES["en"]) == PHRASE_KEYS
    assert SLACK_PHRASES["en"]["not_linked"] == (
        "This Slack account is not linked yet. Sign in to the web app and "
        "<{link_url}|open the link page>, then send the one-time code here."
    )
    assert SLACK_PHRASES["en"]["turn_cut_off"] == "_(interrupted — please try again)_"
    assert SLACK_PHRASES["ru"]["already_linked"] == "Этот Slack-аккаунт уже привязан."


def test_telegram_tables_render_html_and_carry_extras():
    assert set(TELEGRAM_PHRASES["en"]) == PHRASE_KEYS | {"new_conversation"}
    assert TELEGRAM_PHRASES["en"]["not_linked"] == (
        "This Telegram account is not linked yet. Sign in to the web app and "
        '<a href="{link_url}">open the link page</a>, then send the one-time code here.'
    )
    assert TELEGRAM_PHRASES["ru"]["turn_cut_off"] == "<i>(прервано — попробуйте ещё раз)</i>"
    assert TELEGRAM_PHRASES["ru"]["new_conversation"] == "Начат новый диалог. Задавайте вопрос."


def test_phrase_fn_formats_and_falls_back_to_english():
    phrase = make_phrase_fn(
        build_phrases(
            PhraseStyle(
                platform="X",
                link=lambda url, label: f"{label}({url})",
                italic=lambda text: f"*{text}*",
            )
        )
    )
    assert "linked to a@b.test" in phrase("en", "linked", email="a@b.test")
    assert "open the link page(https://x.test/link)" in phrase(
        "de", "not_linked", link_url="https://x.test/link"
    )
    assert phrase("ru", "turn_failed", code="MODEL_DOWN") == (
        "Не удалось получить ответ (MODEL_DOWN). Попробуйте ещё раз."  # noqa: RUF001 — Cyrillic copy next to a Latin code, confusables misfire
    )
