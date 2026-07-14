"""Per-connector webhook verifiers: signature schemes + delivery-id dedup keys (unit)."""

import hashlib
import hmac

import pytest

from achilles.harvester.connectors.confluence import ConfluenceConnector
from achilles.harvester.connectors.gitlab import GitLabConnector
from achilles.harvester.connectors.jira import JiraConnector
from achilles.harvester.connectors.slack import SlackConnector

pytestmark = [pytest.mark.unit, pytest.mark.p1]

SECRET = "s3cr3t-signing-key"
BODY = b'{"event":"changed"}'
NOW = 1_720_000_000.0


def _hmac(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_jira_hmac_scheme() -> None:
    good = {"X-Hub-Signature": f"sha256={_hmac(BODY)}", "X-Atlassian-Webhook-Identifier": "d-1"}
    assert (
        JiraConnector.verify_webhook(raw_body=BODY, headers=good, secret=SECRET, now=NOW) == "d-1"
    )
    # Tampered signature and wrong secret are both refused.
    bad = {"X-Hub-Signature": "sha256=deadbeef"}
    assert JiraConnector.verify_webhook(raw_body=BODY, headers=bad, secret=SECRET, now=NOW) is None
    assert (
        JiraConnector.verify_webhook(raw_body=BODY, headers=good, secret="other", now=NOW) is None
    )


def test_jira_falls_back_to_a_body_fingerprint_without_an_id() -> None:
    headers = {"X-Hub-Signature": f"sha256={_hmac(BODY)}"}
    delivery = JiraConnector.verify_webhook(raw_body=BODY, headers=headers, secret=SECRET, now=NOW)
    assert delivery == hashlib.sha256(BODY).hexdigest()


def test_gitlab_static_token_scheme() -> None:
    ok = {"X-Gitlab-Token": SECRET, "X-Gitlab-Event-UUID": "uuid-9"}
    assert (
        GitLabConnector.verify_webhook(raw_body=BODY, headers=ok, secret=SECRET, now=NOW)
        == "uuid-9"
    )
    wrong = {"X-Gitlab-Token": "nope"}
    assert (
        GitLabConnector.verify_webhook(raw_body=BODY, headers=wrong, secret=SECRET, now=NOW) is None
    )


def test_slack_v0_signature_and_freshness() -> None:
    ts = str(int(NOW))
    base = f"v0:{ts}:".encode() + BODY
    sig = f"v0={hmac.new(SECRET.encode(), base, hashlib.sha256).hexdigest()}"
    fresh = {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig}
    assert (
        SlackConnector.verify_webhook(raw_body=BODY, headers=fresh, secret=SECRET, now=NOW)
        == hashlib.sha256(BODY).hexdigest()
    )
    # A stale timestamp (outside the 5-minute window) fails freshness.
    assert (
        SlackConnector.verify_webhook(raw_body=BODY, headers=fresh, secret=SECRET, now=NOW + 3600)
        is None
    )


def test_connector_without_webhooks_rejects_everything() -> None:
    # Confluence declares webhooks=False → the base no-op verifier refuses.
    assert ConfluenceConnector.manifest.webhooks is False
    assert (
        ConfluenceConnector.verify_webhook(raw_body=BODY, headers={}, secret=SECRET, now=NOW)
        is None
    )
