"""Wire contract of the external tier — illustrative form locked in the design."""

from pydantic import BaseModel, Field

from achilles.public_api.constants import LIMIT_DEFAULT, LIMIT_MAX, QUERY_MAX_CHARS


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=QUERY_MAX_CHARS)
    limit: int = Field(default=LIMIT_DEFAULT, ge=1, le=LIMIT_MAX)


class SearchResultOut(BaseModel):
    title: str | None
    snippet: str | None
    source: str  # source_type — the client renders "Jira · RELEASE-482" itself
    url: str | None
    score: float


class SearchOut(BaseModel):
    results: list[SearchResultOut]
    degraded: bool  # embedder was silent — lexical/graph lists only
