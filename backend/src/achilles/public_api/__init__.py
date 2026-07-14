"""Public API — the external `/public/v1` tier (public-api/index.html).

A curated read-only slice for external clients: findings + sources, never a
synthesized answer. Identity is an API key; ACL applies on top, the key scope
only narrows. MCP wraps the same service for AI clients.
"""
