"""Compose: layout, languages, escaping, strict params — tests.html (P1, unit)."""

import pytest

from achilles.auth.constants import UserRole
from achilles.email.compose import Branding, compose
from achilles.email.constants import EmailKind
from achilles.email.i18n import ROLE_NAMES, STRINGS, Locale, resolve_locale, role_name
from achilles.email.smtp import build_message

pytestmark = [pytest.mark.unit, pytest.mark.p1]

INVITE_PARAMS = {"inviter_name": "Anna Ivanova", "role_name": "Member", "ttl_hours": "48"}
LINK = "https://achilles.test/invite/tok-123"


def test_invite_subject_carries_the_inviter():
    ru = compose(EmailKind.INVITE, locale=Locale.RU, params=INVITE_PARAMS, action_url=LINK)
    en = compose(EmailKind.INVITE, locale=Locale.EN, params=INVITE_PARAMS, action_url=LINK)
    assert ru.subject == "Anna Ivanova приглашает вас в Achilles"
    assert en.subject == "Anna Ivanova invited you to Achilles"


def test_action_link_appears_as_button_and_as_plain_text():
    out = compose(EmailKind.INVITE, locale=Locale.EN, params=INVITE_PARAMS, action_url=LINK)
    assert out.html.count(LINK) == 2, "the button href and the visible fallback line"
    assert LINK in out.text


def test_test_letter_has_no_link():
    out = compose(EmailKind.TEST, locale=Locale.RU)
    assert "href" not in out.html
    assert out.subject == "Achilles — проверка SMTP"


def test_branding_paints_button_and_names_the_product():
    """The admin accent tints the CTA; a dark accent gets white ink, a light one dark."""
    dark = compose(
        EmailKind.INVITE,
        locale=Locale.EN,
        branding=Branding("Antrophic", "#636699"),
        params=INVITE_PARAMS,
        action_url=LINK,
    )
    assert "Antrophic" in dark.subject
    assert "#636699" in dark.html
    assert "color:#ffffff" in dark.html  # readable ink over the dark accent

    light = compose(
        EmailKind.INVITE,
        locale=Locale.EN,
        branding=Branding("Antrophic", "#f5d90a"),
        params=INVITE_PARAMS,
        action_url=LINK,
    )
    assert "color:#1c1c1e" in light.html  # dark ink over the light accent


def test_html_escapes_params_text_does_not():
    sneaky = INVITE_PARAMS | {"inviter_name": 'Eve <img src=x onerror="pwn()">'}
    out = compose(EmailKind.INVITE, locale=Locale.EN, params=sneaky, action_url=LINK)
    assert "<img" not in out.html
    assert "&lt;img" in out.html
    assert "<img" in out.text  # the plain-text part renders raw on purpose


def test_missing_param_is_a_hard_error():
    with pytest.raises(KeyError):
        compose(EmailKind.RESET, locale=Locale.EN, params={}, action_url=LINK)


def test_locale_parity_and_catalog_coverage():
    for kind, per_locale in STRINGS.items():
        assert set(per_locale) == {Locale.RU, Locale.EN}, kind
        ru, en = per_locale[Locale.RU], per_locale[Locale.EN]
        assert (ru.action is None) == (en.action is None), kind
    assert {kind.value for kind in EmailKind} == set(STRINGS)
    for locale in (Locale.RU, Locale.EN):
        assert set(ROLE_NAMES[locale]) == {r.value for r in UserRole}


def test_resolve_locale_falls_back_to_ru():
    assert resolve_locale("en") is Locale.EN
    assert resolve_locale(None) is Locale.RU
    assert resolve_locale("zz") is Locale.RU
    assert role_name("member", Locale.RU) == "Участник"
    assert role_name("unknown-role", Locale.EN) == "unknown-role"


def test_message_is_multipart_alternative_with_rfc5322_from():
    out = compose(EmailKind.RESET, locale=Locale.EN, params={"ttl_hours": "1"}, action_url=LINK)
    message = build_message(out, to="person@example.com", from_address="Achilles <no-reply@x.y>")
    assert message["From"] == "Achilles <no-reply@x.y>"
    assert message["To"] == "person@example.com"
    assert message["Subject"] == out.subject
    parts = [part.get_content_type() for part in message.iter_parts()]
    assert parts == ["text/plain", "text/html"]
