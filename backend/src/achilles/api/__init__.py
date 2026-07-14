"""HTTP API layer.

The mount points below are the single source of the base path — consumers
compose full paths from them instead of re-spelling "/api/v1" by hand.
"""

API_PREFIX = "/api"
V1_PREFIX = "/v1"
API_V1 = API_PREFIX + V1_PREFIX
PUBLIC_V1 = "/public/v1"  # external tier, mounted at the root — apart from /api
