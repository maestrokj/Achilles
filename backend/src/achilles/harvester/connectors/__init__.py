"""Connector plugins — one per source *type* (connectors.html).

Design deviation, recorded: the catalog line says "on top of the official
SDK", but v1 connectors are thin async httpx clients over the sources' REST
APIs instead. The official SDKs are synchronous (requests-based) and carry
their own retry layers, which would fight the platform's reliability contract
(AIMD, Retry-After, error_class run on the raw HTTP response — connectors.html
manifest hooks). An SDK-based connector still plugs in unchanged through the
entry-point channel: the contract exposes no HTTP.
"""
