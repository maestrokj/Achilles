"""Shared ground of the Atlassian Cloud pair (connectors.html#jira, #confluence).

Jira and Confluence Cloud share the auth shape (Basic from the admin-pasted
"email:api_token"), the timestamp format and the "markup in, flat text out"
need. Endpoints and payload shapes stay in each connector.
"""

import base64
import html
import re
from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from typing import ClassVar, Self, cast

from achilles.harvester.connectors.base import (
    BaseConnector,
    Diagnosis,
    DiagnosisStep,
    build_diagnosis,
)
from achilles.harvester.connectors.http import (
    SourceHttpClient,
    SourceItemError,
    SourceUnavailableError,
    Throttle,
)

# JQL/CQL datetime literals cannot carry an offset and are evaluated in the
# API user's profile timezone (Jira, JRACLOUD-74279) / the instance timezone
# (Confluence, CONFCLOUD-70966), not UTC — a zone west of UTC would shift the
# incremental threshold forward and silently skip updates. Rendering
# (since - SINCE_TZ_SKEW) keeps the threshold behind the true watermark for
# any zone down to UTC-12, the westernmost offset; the overlap re-read is an
# idempotent upsert, so the extra window only costs a few duplicate reads.
SINCE_TZ_SKEW = timedelta(hours=12)

# Best-effort flattening with plain regexes — no HTML-parser dependency (v1).
_BLOCK_BREAK_RE = re.compile(
    r"<br\s*/?>|</(?:p|div|li|tr|h[1-6]|blockquote|pre)\s*>", re.IGNORECASE
)
_TAG_RE = re.compile(r"<[^>]+>")
_WIKI_MACRO_RE = re.compile(r"\{[a-zA-Z][^{}]*\}")  # {code}, {panel:...}, {noformat}


def basic_auth_header(credential: str) -> dict[str, str]:
    """Authorization header from the admin-pasted "email:api_token" credential."""
    token = base64.b64encode(credential.encode()).decode()
    return {"Authorization": f"Basic {token}"}


def parse_atlassian_datetime(value: str) -> datetime:
    """Parse "2026-07-01T12:00:00.000+0000" / ISO 8601 into an aware UTC datetime."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def strip_markup(text: str) -> str:
    """Flatten storage HTML / wiki markup to plain text.

    Block boundaries become newlines, remaining tags and {macro} markers are
    dropped, entities are decoded, runs of blank lines collapse to one.
    """
    flat = _BLOCK_BREAK_RE.sub("\n", text)
    flat = _TAG_RE.sub("", flat)
    flat = _WIKI_MACRO_RE.sub("", flat)
    flat = html.unescape(flat)
    lines = [" ".join(line.split()) for line in flat.splitlines()]
    collapsed: list[str] = []
    for line in lines:
        if line or (collapsed and collapsed[-1]):
            collapsed.append(line)
    while collapsed and not collapsed[-1]:
        collapsed.pop()
    return "\n".join(collapsed)


# --- Tolerant JSON navigation: shape drift yields empties, never raises ---


def as_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return cast("dict[str, object]", value)
    return {}


def as_list(value: object) -> list[object]:
    if isinstance(value, list):
        return cast("list[object]", value)
    return []


def as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


class AtlassianConnector(BaseConnector, ABC):
    """Common construction and stepped probe for the Atlassian Cloud connectors."""

    # Auth-sensitive endpoint probed for the reachability + credentials steps.
    identity_probe_path: ClassVar[str]

    def __init__(
        self,
        *,
        client: SourceHttpClient,
        base_url: str,
        scope_mode: str = "all",
        scope_list: tuple[str, ...] = (),
        content_filters: dict[str, object] | None = None,
    ) -> None:
        super().__init__(
            scope_mode=scope_mode, scope_list=scope_list, content_filters=content_filters
        )
        self.client = client
        self.base_url = base_url

    @classmethod
    def create(
        cls,
        *,
        base_url: str | None,
        credential: str,
        throttle: Throttle | None = None,
        scope_mode: str = "all",
        scope_list: tuple[str, ...] = (),
        content_filters: dict[str, object] | None = None,
    ) -> Self:
        root = (base_url or "").rstrip("/")
        client = SourceHttpClient(
            base_url=root,
            headers=basic_auth_header(credential),
            throttle=throttle,
            request_cost=cls.manifest.request_cost,
        )
        return cls(
            client=client,
            base_url=root,
            scope_mode=scope_mode,
            scope_list=scope_list,
            content_filters=content_filters,
        )

    async def aclose(self) -> None:
        await self.client.aclose()

    @abstractmethod
    async def _probe_permissions(self) -> DiagnosisStep:
        """Third probe step — the cheapest call proving content is actually visible."""

    async def check_connection(self) -> Diagnosis:
        try:
            await self.client.get_json(self.identity_probe_path)
        except SourceUnavailableError as exc:
            return build_diagnosis("reachability", str(exc))
        except SourceItemError as exc:
            return build_diagnosis("credentials", exc.detail or str(exc))
        # Success path stays hand-built: the third step comes from _probe_permissions
        # and may fail with its own detail, which the ladder helper cannot express.
        return Diagnosis(
            steps=(
                DiagnosisStep(name="reachability", ok=True),
                DiagnosisStep(name="credentials", ok=True),
                await self._probe_permissions(),
            )
        )
