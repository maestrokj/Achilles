"""Webhook delivery: payload per preset, HMAC for generic, the test probe.

Only broadcast events travel over webhooks; the payload speaks the org
language. Security: http(s) only, no redirects, short timeout, the secret
signs the exact body bytes (generic preset).
"""

import asyncio
import hashlib
import hmac
import ipaddress
import json
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx

from achilles.auth.security.crypto import decrypt
from achilles.email.i18n import Locale
from achilles.notifications.constants import EventType, Severity, WebhookPreset
from achilles.notifications.i18n import render
from achilles.notifications.models import Notification, NotificationChannel

WEBHOOK_TIMEOUT_SECONDS = 10.0
SIGNATURE_HEADER = "X-Achilles-Signature"


class WebhookDeliveryError(Exception):
    """The endpoint refused or was unreachable."""


def _is_blocked_ip(ip: str) -> bool:
    """Reject anything that isn't a routable public address (SSRF guard)."""
    addr = ipaddress.ip_address(ip)
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local  # incl. 169.254.169.254 cloud metadata
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


async def _resolve_host(host: str) -> list[str]:
    """Resolve a host to its IPs (the network seam webhook tests stub out)."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, None)
    return [info[4][0] for info in infos]


async def _assert_public_url(url: str) -> None:
    """Block webhooks aimed at internal/loopback/metadata hosts before we connect.

    Admin-only, but a webhook URL could still be pointed at ``localhost``,
    ``169.254.169.254``, or an internal service. We resolve the host and refuse
    if any resolved address is non-public. Best-effort against DNS rebinding —
    the connect re-resolves — but it closes the obvious SSRF vectors.
    """
    host = urlsplit(url).hostname
    if not host:
        raise WebhookDeliveryError("bad_host")
    try:
        ips = await _resolve_host(host)
    except OSError as exc:
        raise WebhookDeliveryError(f"dns: {exc}") from exc
    if any(_is_blocked_ip(ip) for ip in ips):
        raise WebhookDeliveryError("blocked_host")


def build_payload(
    preset: WebhookPreset, notification: Notification, *, locale: Locale
) -> dict[str, object]:
    rendered = render(notification.title, notification.title_params, locale)
    if preset is WebhookPreset.SLACK:
        text = f"*{rendered.title}*"
        if rendered.body:
            text += f"\n{rendered.body}"
        return {"text": text}
    return {
        "event": notification.title,
        "severity": notification.severity,
        "title": rendered.title,
        "source": notification.source,
        "source_ref": notification.source_ref,
        "ts": (notification.created_at or datetime.now(UTC)).isoformat(),
    }


async def post(channel: NotificationChannel, payload: dict[str, object], *, key: bytes) -> None:
    """One POST to the channel endpoint; raises WebhookDeliveryError on any failure."""
    if not channel.url_enc:
        raise WebhookDeliveryError("no_url")
    url = decrypt(channel.url_enc, key=key)
    if not url.startswith(("http://", "https://")):
        raise WebhookDeliveryError("bad_scheme")
    await _assert_public_url(url)

    body = json.dumps(payload, ensure_ascii=False).encode()
    headers = {"Content-Type": "application/json"}
    if channel.secret_enc and channel.webhook_preset is WebhookPreset.GENERIC:
        secret = decrypt(channel.secret_enc, key=key)
        digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        headers[SIGNATURE_HEADER] = f"sha256={digest}"

    try:
        async with httpx.AsyncClient(
            timeout=WEBHOOK_TIMEOUT_SECONDS, follow_redirects=False
        ) as client:
            response = await client.post(url, content=body, headers=headers)
    except httpx.HTTPError as exc:
        raise WebhookDeliveryError(f"network: {exc}") from exc
    if response.status_code >= 300:
        raise WebhookDeliveryError(f"http_{response.status_code}")


def test_payload(preset: WebhookPreset, *, locale: Locale) -> dict[str, object]:
    """A fabricated event for the Test button — the real payload builder, a probe row."""
    probe = Notification(
        event_type=EventType.SYSTEM.value,
        severity=Severity.INFO.value,
        title="system.channel_test",
        title_params={},
        source="admin",
    )
    return build_payload(preset, probe, locale=locale)
