"""Letter strings, RU/EN — server-side, separate from the frontend locales.

Design: email/_workzone/templates.html. Typed keys, no user-facing string in
logic code; reset/test speak the recipient's language, an invite speaks the
org default (the invitee has no profile yet).
"""

from dataclasses import dataclass

from achilles.auth.constants import Locale, UserRole


@dataclass(frozen=True, slots=True)
class LetterStrings:
    """One letter in one language; `body` holds `{param}` placeholders."""

    subject: str
    body: str
    action: str | None  # button label; None → a letter without a link
    footer: str


_INVITE = {
    Locale.RU: LetterStrings(
        subject="{inviter_name} приглашает вас в {product}",
        body=(
            "{inviter_name} приглашает вас в {product} — роль «{role_name}». "
            "Создайте аккаунт по ссылке ниже, она действует {ttl_hours} ч."
        ),
        action="Принять приглашение",
        footer="Не ждали приглашения? Просто проигнорируйте письмо.",
    ),
    Locale.EN: LetterStrings(
        subject="{inviter_name} invited you to {product}",
        body=(
            "{inviter_name} invited you to {product} as {role_name}. "
            "Create your account below — the link is valid for {ttl_hours} h."
        ),
        action="Accept invitation",
        footer="Didn't expect this? You can safely ignore this email.",
    ),
}

_RESET = {
    Locale.RU: LetterStrings(
        subject="Сброс пароля — {product}",
        body=(
            "Вы запросили сброс пароля. Задайте новый по ссылке ниже — она действует {ttl_hours} ч."
        ),
        action="Задать новый пароль",
        footer="Не запрашивали сброс? Проигнорируйте письмо — пароль не изменится.",
    ),
    Locale.EN: LetterStrings(
        subject="Reset your {product} password",
        body=(
            "You requested a password reset. Set a new one below — "
            "the link is valid for {ttl_hours} h."
        ),
        action="Set new password",
        footer="Didn't request this? Ignore this email — your password stays unchanged.",
    ),
}

_TEST = {
    Locale.RU: LetterStrings(
        subject="{product} — проверка SMTP",
        body="Письмо доставлено — отправка почты настроена верно.",
        action=None,
        footer="Отправлено кнопкой «Проверить соединение» в настройках.",
    ),
    Locale.EN: LetterStrings(
        subject="{product} SMTP test",
        body="This email arrived — outgoing mail is configured correctly.",
        action=None,
        footer="Sent by the “Test connection” button in settings.",
    ),
}

# Notification letters render a pre-localized title/body from the Notifications
# catalog — the letter itself only frames them (arrives with stage 9b).
_NOTIFICATION = {
    Locale.RU: LetterStrings(
        subject="{title}",
        body="{body}",
        action="Открыть в {product}",
        footer="Уведомление {product}. Подписки — в профиле.",
    ),
    Locale.EN: LetterStrings(
        subject="{title}",
        body="{body}",
        action="Open in {product}",
        footer="{product} notification. Manage subscriptions in your profile.",
    ),
}

STRINGS: dict[str, dict[Locale, LetterStrings]] = {
    "invite": _INVITE,
    "reset": _RESET,
    "test": _TEST,
    "notification": _NOTIFICATION,
}

# Role display names for the invite letter (the wire keeps raw role slugs).
ROLE_NAMES: dict[Locale, dict[str, str]] = {
    Locale.RU: {
        UserRole.OWNER.value: "Владелец",
        UserRole.ADMIN.value: "Администратор",
        UserRole.MEMBER.value: "Участник",
    },
    Locale.EN: {
        UserRole.OWNER.value: "Owner",
        UserRole.ADMIN.value: "Admin",
        UserRole.MEMBER.value: "Member",
    },
}


def resolve_locale(value: str | None) -> Locale:
    """A tolerant locale coercion: unknown/missing → RU (the org default default)."""
    try:
        return Locale(value) if value else Locale.RU
    except ValueError:
        return Locale.RU


def role_name(role: str, locale: Locale) -> str:
    return ROLE_NAMES[locale].get(role, role)
