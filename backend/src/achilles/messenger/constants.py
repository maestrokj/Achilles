"""Knobs every messenger intake shares, whatever the transport.

The per-sender window is deliberately generous: it exists to absorb a runaway
script or a retry storm, not to police a person typing.
"""

WEBHOOK_RATE_LIMIT = 60  # inbound messages per sliding minute per sender scope
WEBHOOK_RATE_WINDOW_SECONDS = 60
