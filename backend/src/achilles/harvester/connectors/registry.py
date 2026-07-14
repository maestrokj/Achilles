"""Connector type registry: self-registration + discovery (connectors.html#registry).

Types register with the @register_connector decorator at import time; discovery
walks the built-in package plus the `achilles.connectors` entry-point group
(the canonical channel for pip-installed custom connectors). Runs once per
process — a new connector type appears after an image rebuild, by design.
"""

import importlib
import logging
import pkgutil
from importlib.metadata import entry_points

from achilles.harvester.connectors.base import BaseConnector

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "achilles.connectors"

_registry: dict[str, type[BaseConnector]] = {}


def register_connector[C: BaseConnector](cls: type[C]) -> type[C]:
    name = cls.manifest.type
    if name in _registry:
        msg = f"duplicate connector type {name!r}"
        raise ValueError(msg)
    _registry[name] = cls
    return cls


def registered_connectors() -> dict[str, type[BaseConnector]]:
    return dict(_registry)


def get_connector_type(name: str) -> type[BaseConnector] | None:
    return _registry.get(name)


def discover_connectors() -> None:
    """Import everything that self-registers; idempotent, called at startup."""
    pkg = importlib.import_module("achilles.harvester.connectors")
    for module in pkgutil.iter_modules(pkg.__path__):
        importlib.import_module(f"{pkg.__name__}.{module.name}")
    for entry_point in entry_points(group=ENTRY_POINT_GROUP):
        try:
            entry_point.load()
        except Exception:
            logger.exception("connector entry point %s failed to load", entry_point.name)
