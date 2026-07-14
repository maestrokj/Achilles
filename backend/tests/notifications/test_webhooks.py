"""Webhook payloads and transport: presets, HMAC, failure taxonomy (integration)."""

import hashlib
import hmac
import json

import pytest
import respx
from httpx import Response

from achilles.auth.security.crypto import derive_crypto_key, encrypt
from achilles.notifications import webhooks
from achilles.notifications.constants import WebhookPreset
from achilles.notifications.i18n import Locale
from achilles.notifications.models import Notification, NotificationChannel

pytestmark = [pytest.mark.integration, pytest.mark.p1]

KEY = derive_crypto_key(crypto_key="", secret_key="unit-test-secret")
URL = "https://hooks.example.test/achilles"


def make_notification(**overrides: object) -> Notification:
    values: dict[str, object] = {
        "event_type": "sync",
        "severity": "critical",
        "title": "sync.source_unreachable",
        "title_params": {"source_name": "Confluence"},
        "source": "harvester",
        "source_ref": "source/5",
    }
    values.update(overrides)
    return Notification(**values)


def make_channel(
    *,
    preset: str = "generic",
    secret: str | None = "hook-secret",  # noqa: S107 — test fixture value
) -> NotificationChannel:
    return NotificationChannel(
        kind="webhook",
        preset=preset,
        name="Ops",
        url_enc=encrypt(URL, key=KEY),
        secret_enc=encrypt(secret, key=KEY) if secret else None,
        enabled=True,
    )


def test_generic_payload_is_neutral_json():
    payload = webhooks.build_payload(WebhookPreset.GENERIC, make_notification(), locale=Locale.EN)
    assert payload["event"] == "sync.source_unreachable"
    assert payload["severity"] == "critical"
    assert payload["title"] == "Source “Confluence” is unreachable"
    assert payload["source"] == "harvester"
    assert payload["source_ref"] == "source/5"
    assert "ts" in payload


def test_slack_payload_is_mrkdwn_text():
    payload = webhooks.build_payload(WebhookPreset.SLACK, make_notification(), locale=Locale.RU)
    text = str(payload["text"])
    assert text.startswith("*Источник «Confluence» недоступен*")
    assert "\n" in text  # the body line follows the title


async def test_generic_post_signs_the_exact_body(hibp_clean: respx.MockRouter):
    route = hibp_clean.post(URL).mock(return_value=Response(200))
    channel = make_channel()
    payload = webhooks.build_payload(WebhookPreset.GENERIC, make_notification(), locale=Locale.EN)

    await webhooks.post(channel, payload, key=KEY)

    request = route.calls[0].request
    body = request.content
    expected = hmac.new(b"hook-secret", body, hashlib.sha256).hexdigest()
    assert request.headers[webhooks.SIGNATURE_HEADER] == f"sha256={expected}"
    assert json.loads(body)["title"]


async def test_slack_preset_sends_no_signature(hibp_clean: respx.MockRouter):
    route = hibp_clean.post(URL).mock(return_value=Response(200))
    channel = make_channel(preset="slack")
    await webhooks.post(channel, {"text": "hi"}, key=KEY)
    assert webhooks.SIGNATURE_HEADER not in route.calls[0].request.headers


async def test_http_refusal_and_bad_scheme_raise(hibp_clean: respx.MockRouter):
    hibp_clean.post(URL).mock(return_value=Response(500))
    with pytest.raises(webhooks.WebhookDeliveryError, match="http_500"):
        await webhooks.post(make_channel(), {"a": 1}, key=KEY)

    ftp_channel = make_channel()
    ftp_channel.url_enc = encrypt("ftp://internal/x", key=KEY)
    with pytest.raises(webhooks.WebhookDeliveryError, match="bad_scheme"):
        await webhooks.post(ftp_channel, {"a": 1}, key=KEY)


@pytest.mark.parametrize(
    ("ip", "blocked"),
    [
        ("127.0.0.1", True),  # loopback
        ("10.0.0.5", True),  # private
        ("192.168.1.1", True),  # private
        ("169.254.169.254", True),  # link-local / cloud metadata
        ("::1", True),  # IPv6 loopback
        ("93.184.216.34", False),  # public
        ("8.8.8.8", False),  # public
    ],
)
def test_is_blocked_ip(ip: str, blocked: bool):
    assert webhooks._is_blocked_ip(ip) is blocked


async def test_assert_public_url_blocks_internal_target(monkeypatch: pytest.MonkeyPatch):
    async def _resolve_internal(_host: str) -> list[str]:
        return ["127.0.0.1"]

    monkeypatch.setattr(webhooks, "_resolve_host", _resolve_internal)
    with pytest.raises(webhooks.WebhookDeliveryError, match="blocked_host"):
        await webhooks.post(make_channel(), {"a": 1}, key=KEY)


def test_test_payload_mirrors_the_real_shape():
    generic = webhooks.test_payload(WebhookPreset.GENERIC, locale=Locale.EN)
    assert generic["event"] == "system.channel_test"
    slack = webhooks.test_payload(WebhookPreset.SLACK, locale=Locale.RU)
    assert "text" in slack
