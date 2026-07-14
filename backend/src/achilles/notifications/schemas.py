"""Wire contracts: the feed, personal prefs, the admin config screen."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# --- Feed ---


class NotificationOut(BaseModel):
    id: int
    event: str  # the raw catalog key (the client may deep-link by it)
    event_type: str
    severity: str
    title: str  # rendered in the reader's language
    body: str | None
    source: str | None
    source_ref: str | None
    dedup_count: int
    created_at: datetime
    last_seen_at: datetime | None
    read_at: datetime | None


class UnreadOut(BaseModel):
    count: int


# --- Personal prefs ---


class Pref(BaseModel):
    """One cell, both directions — GET returns and PUT accepts the same shape."""

    event_type: str
    in_app_enabled: bool
    email_enabled: bool


class Prefs(BaseModel):
    items: list[Pref]


# --- Admin: channels + routes ---


class ChannelOut(BaseModel):
    id: int
    kind: str
    preset: str | None
    name: str
    is_builtin: bool
    enabled: bool
    url_mask: str | None  # ••••xxxx, None = not set
    secret_set: bool
    last_test_ok: bool | None
    last_test_at: datetime | None


class ChannelsOut(BaseModel):
    items: list[ChannelOut]


class ChannelCreate(BaseModel):
    """Admin webhooks only — the builtin pair is seeded, never created."""

    name: str = Field(min_length=1)
    preset: Literal["slack", "generic"]
    url: str
    secret: str | None = None


class ChannelPatch(BaseModel):
    """Partial by model_fields_set; an empty-string secret clears the column."""

    name: str | None = None
    url: str | None = None
    secret: str | None = None
    enabled: bool | None = None


class ChannelTestOut(BaseModel):
    ok: bool
    error: str | None = None


class RouteOut(BaseModel):
    event_type: str
    severity: str  # the category's loudest catalog event — the matrix row badge
    channel_id: int
    enabled: bool
    locked: bool


class RoutesOut(BaseModel):
    items: list[RouteOut]


class RouteCellPatch(BaseModel):
    event_type: str
    channel_id: int
    enabled: bool


class RoutesPatch(BaseModel):
    items: list[RouteCellPatch]
