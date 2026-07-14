"""Prompt override validation: placeholder whitelist, length cap (unit)."""

import pytest

from achilles.ai_foundation.constants import PROMPT_MAX_CHARS
from achilles.ai_foundation.prompt_texts import DEFAULT_PROMPTS
from achilles.ai_foundation.services.prompt import validate_text
from achilles.api.problems import ApiError

pytestmark = [pytest.mark.unit, pytest.mark.p1]


@pytest.mark.parametrize(
    "text",
    [
        "Plain text without placeholders.",
        "Company {org_name}, date {today}.",
        "{today}{org_name}",
    ],
)
def test_known_placeholders_pass(text: str):
    assert validate_text(text, field="org_text") == text


@pytest.mark.parametrize("text", ["Hello {user}", "{secret_key}", 'code {"a": 1}'])
def test_unknown_placeholder_dies(text: str):
    with pytest.raises(ApiError) as exc_info:
        validate_text(text, field="org_text")
    assert exc_info.value.status == 422
    assert exc_info.value.code == "UNKNOWN_PLACEHOLDER"


def test_cap_enforced():
    with pytest.raises(ApiError) as exc_info:
        validate_text("x" * (PROMPT_MAX_CHARS + 1), field="safety_text")
    assert exc_info.value.status == 422


def test_defaults_fit_their_own_rules():
    for locale_texts in DEFAULT_PROMPTS.values():
        for field, text in locale_texts.items():
            assert validate_text(text, field=field) == text
