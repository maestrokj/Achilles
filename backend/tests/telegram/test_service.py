"""Unit coverage for the pure webhook helpers (no DB, no network)."""

import pytest

from achilles.telegram.service import webhook_base_is_public

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://achilles.example.com", True),
        ("https://bot.trycloudflare.com", True),
        ("https://8.8.8.8", True),  # a public literal IP
        ("http://achilles.example.com", False),  # plain http can't receive
        ("https://localhost", False),
        ("https://localhost:3000", False),
        ("http://localhost:3000", False),
        ("https://dev.local", False),  # mDNS name, not routable
        ("https://127.0.0.1", False),  # loopback
        ("https://10.0.0.5", False),  # private range
        ("https://192.168.1.20", False),  # private range
        ("https://[::1]", False),  # IPv6 loopback
        ("", False),
        ("not-a-url", False),
    ],
)
def test_webhook_base_is_public(base_url: str, expected: bool):
    assert webhook_base_is_public(base_url) is expected
