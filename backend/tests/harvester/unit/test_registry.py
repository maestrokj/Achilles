"""Connector registry: registration, duplicates, discovery (unit)."""

import sys
from collections.abc import AsyncIterator
from datetime import datetime

import pytest

from achilles.harvester.connectors import registry
from achilles.harvester.connectors.base import (
    BaseConnector,
    ConnectorManifest,
    Diagnosis,
    GroupDraft,
    NormalizedEntity,
    PrincipalDraft,
    RawItem,
    ScopeObject,
)

pytestmark = [pytest.mark.unit, pytest.mark.p1]


def _make_connector(type_name: str) -> type[BaseConnector]:
    class Stub(BaseConnector):
        manifest = ConnectorManifest(
            type=type_name,
            title=type_name.title(),
            needs_base_url=True,
            credential_label="API token",
            scope_kinds=("project",),
        )

        async def fetch(self, since: datetime | None) -> AsyncIterator[RawItem]:
            raise NotImplementedError
            yield  # pragma: no cover

        def normalize(self, raw: RawItem) -> NormalizedEntity:
            raise NotImplementedError

        async def fetch_principals(self) -> AsyncIterator[PrincipalDraft]:
            raise NotImplementedError
            yield  # pragma: no cover

        async def fetch_groups(self) -> AsyncIterator[GroupDraft]:
            raise NotImplementedError
            yield  # pragma: no cover

        async def list_catalog(self) -> list[ScopeObject]:
            raise NotImplementedError

        async def check_connection(self) -> Diagnosis:
            raise NotImplementedError

    return Stub


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "_registry", {})


def test_register_and_lookup() -> None:
    cls = registry.register_connector(_make_connector("stub"))
    assert registry.get_connector_type("stub") is cls
    assert "stub" in registry.registered_connectors()


def test_duplicate_type_raises() -> None:
    registry.register_connector(_make_connector("stub"))
    with pytest.raises(ValueError, match="duplicate connector type"):
        registry.register_connector(_make_connector("stub"))


def test_unknown_type_is_none() -> None:
    assert registry.get_connector_type("nope") is None


def test_discover_registers_atlassian_connectors() -> None:
    # Modules already imported elsewhere would not re-run their decorators
    # against the isolated registry — force a fresh import.
    for name in ("jira", "confluence"):
        sys.modules.pop(f"achilles.harvester.connectors.{name}", None)
    registry.discover_connectors()
    assert {"jira", "confluence"} <= registry.registered_connectors().keys()
