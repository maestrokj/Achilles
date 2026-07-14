"""Wire format for datetimes: UTC ISO-8601 with a ``Z`` suffix, never an offset."""

from datetime import UTC, datetime
from typing import Annotated

from pydantic import PlainSerializer


def to_utc_z(dt: datetime) -> str:
    """Serialize as ``…Z``; naive input is a bug upstream (UTC-aware everywhere)."""
    if dt.tzinfo is None:
        msg = "naive datetime reached the API serialization layer"
        raise ValueError(msg)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


UtcDateTime = Annotated[datetime, PlainSerializer(to_utc_z, return_type=str, when_used="json")]
