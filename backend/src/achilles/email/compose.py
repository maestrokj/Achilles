"""Compose step: template + data + branding → subject, HTML and text versions.

One layout for every letter (templates.html#layout); a letter is a string set
from i18n plus params. A missing param is a hard error (StrictUndefined /
KeyError) — a half-substituted letter must never leave the building. Branding
(org name + accent colour) comes from platform_settings so every letter wears
the workspace's own colour; a default keeps unit tests and any brandless caller
rendering the stock look.
"""

from dataclasses import dataclass

from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape

from achilles.email.constants import EmailKind
from achilles.email.i18n import STRINGS, Locale

# Stock branding: the seed defaults (platform_settings.org_name / accent_color).
DEFAULT_PRODUCT_NAME = "Achilles"
DEFAULT_ACCENT_COLOR = "#6366f1"

# Above this relative luminance the accent is light enough to carry dark ink.
LUMINANCE_LIGHT_THRESHOLD = 0.5


@dataclass(frozen=True, slots=True)
class Branding:
    """The workspace face a letter wears — org name and accent colour."""

    product_name: str = DEFAULT_PRODUCT_NAME
    accent_color: str = DEFAULT_ACCENT_COLOR


DEFAULT_BRANDING = Branding()

# Autoescape guards the HTML template only; the text alternative renders raw.
_env = Environment(
    loader=PackageLoader("achilles.email", "templates"),
    autoescape=select_autoescape(enabled_extensions=("html.j2",), default=False),
    undefined=StrictUndefined,
)


@dataclass(frozen=True, slots=True)
class ComposedEmail:
    subject: str
    html: str
    text: str


def _on_accent_text(accent: str) -> str:
    """Readable label colour over the accent button (WCAG relative luminance).

    Dark ink only on a genuinely light accent; otherwise white — the button
    must stay legible whatever colour the admin picks. Formula and threshold
    mirror the frontend's `relativeLuminance` (app-shell/DisplayPrefs.tsx) so
    the same accent gets the same ink in the letter and in the app.
    """

    def channel(raw: int) -> float:
        value = raw / 255
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    try:
        r, g, b = (channel(int(accent[i : i + 2], 16)) for i in (1, 3, 5))
    except ValueError:  # pragma: no cover — column is CHECK-constrained to a hex colour
        return "#ffffff"
    luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return "#1c1c1e" if luminance > LUMINANCE_LIGHT_THRESHOLD else "#ffffff"


def compose(
    kind: EmailKind,
    *,
    locale: Locale,
    branding: Branding = DEFAULT_BRANDING,
    params: dict[str, str] | None = None,
    action_url: str | None = None,
) -> ComposedEmail:
    """Render one letter; params fill the placeholders, autoescape guards HTML."""
    strings = STRINGS[kind.value][locale]
    values = {"product": branding.product_name, **(params or {})}
    subject = strings.subject.format(**values)
    body = strings.body.format(**values)
    context = {
        "product_name": branding.product_name,
        "accent": branding.accent_color,
        "on_accent": _on_accent_text(branding.accent_color),
        "preheader": body,
        "body": body,
        "footer": strings.footer.format(**values),
        "action_label": strings.action.format(**values) if strings.action else None,
        "action_url": action_url if strings.action else None,
    }
    return ComposedEmail(
        subject=subject,
        html=_env.get_template("letter.html.j2").render(**context),
        text=_env.get_template("letter.txt.j2").render(**context),
    )
