"""Reusable Pydantic field validators shared across the API surface."""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def validate_iana_timezone(value: str | None) -> str | None:
    """Accept only a known IANA zone name (``None`` passes through unchanged)."""
    if value is None:
        return None
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        msg = "unknown IANA timezone"
        raise ValueError(msg) from exc
    return value
