"""Wire contracts of the agent routes + the schedule vocabulary.

The schedule JSONB is a discriminated union with the backend as the source of
truth (data-model.html#agents): interval — "every N hours from the last run";
calendar — a daily/weekly slot in the owner's timezone. The status chip is
derived, never stored.
"""

from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from achilles.agent_engine.constants import AgentStatus, CalendarCadence, ScheduleKind
from achilles.api.serialization import UtcDateTime

MAX_AGENT_NAME_CHARS = 200
MAX_AGENT_PROMPT_CHARS = 20_000
TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"

# --- Schedule (stored in agents.schedule, validated on the way in) ---


class IntervalSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal[ScheduleKind.INTERVAL]
    every_hours: int = Field(ge=1, le=24)


class CalendarSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal[ScheduleKind.CALENDAR]
    cadence: CalendarCadence
    weekday: int | None = Field(default=None, ge=0, le=6)  # 0 = Monday; weekly only
    time: str = Field(pattern=TIME_PATTERN)  # local to the owner's timezone

    @model_validator(mode="after")
    def _weekly_needs_weekday(self) -> Self:
        if self.cadence == CalendarCadence.WEEKLY and self.weekday is None:
            msg = "weekday is required for weekly cadence"
            raise ValueError(msg)
        return self


type ScheduleSpec = IntervalSchedule | CalendarSchedule
# The union is only ever handled tagged — every field and the adapter share
# this one wiring, so a third schedule kind is added in a single place.
ScheduleField = Annotated[ScheduleSpec, Field(discriminator="type")]

SCHEDULE_ADAPTER: TypeAdapter[ScheduleSpec] = TypeAdapter(ScheduleField)


def parse_schedule(raw: dict[str, Any] | None) -> ScheduleSpec | None:
    """Stored JSONB → spec; rows are written through the same adapter."""
    return SCHEDULE_ADAPTER.validate_python(raw) if raw is not None else None


# --- Requests ---


class AgentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=MAX_AGENT_NAME_CHARS)
    description: str | None = None
    prompt: str = Field(min_length=1, max_length=MAX_AGENT_PROMPT_CHARS)
    schedule: ScheduleField | None = None
    model_id: int | None = None  # None → preset to the list default
    tool_ids: list[int] = Field(default_factory=list)


class AgentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=MAX_AGENT_NAME_CHARS)
    description: str | None = None
    prompt: str | None = Field(default=None, min_length=1, max_length=MAX_AGENT_PROMPT_CHARS)
    schedule: ScheduleField | None = None
    model_id: int | None = None
    enabled: bool | None = None
    tool_ids: list[int] | None = None


class AgentLimitsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iteration_cap: int | None = Field(default=None, gt=0, le=2_147_483_647)  # int4 column
    max_concurrency: int | None = Field(default=None, gt=0, le=2_147_483_647)


# --- Responses ---


class RunOut(BaseModel):
    id: int
    trigger: str
    state: str
    reason: str | None
    output: str | None
    tokens_used: int
    error: str | None
    started_at: UtcDateTime | None
    finished_at: UtcDateTime | None
    duration_seconds: int | None  # None while the run never entered running
    created_at: UtcDateTime


class LastRunOut(BaseModel):
    state: str
    reason: str | None
    finished_at: UtcDateTime | None
    duration_seconds: int | None  # None while the run never entered running
    tokens_used: int


class AgentToolOptionOut(BaseModel):
    id: int  # tools row id
    name: str


class AgentOut(BaseModel):
    id: int
    name: str
    description: str | None
    prompt: str
    schedule: ScheduleField | None
    model_id: int | None
    enabled: bool
    admin_paused: bool
    status: AgentStatus  # derived summary chip
    tool_ids: list[int]
    # Selected tools an admin has since disallowed for agents (agents_allowed=false).
    # Kept on the agent and shown disabled in the editor, never silently dropped.
    disabled_tools: list[AgentToolOptionOut]
    next_run_at: UtcDateTime | None
    last_run: LastRunOut | None
    created_at: UtcDateTime


class BudgetOut(BaseModel):
    used: int  # tokens finished inside the current week window
    limit: int | None  # None → no ceiling configured
    week_resets_at: UtcDateTime


class AgentListOut(BaseModel):
    items: list[AgentOut]
    budget: BudgetOut


class AgentModelOptionOut(BaseModel):
    id: int  # agent_models row id — what agents.model_id references
    display_name: str
    is_default: bool


class AgentOptionsOut(BaseModel):
    models: list[AgentModelOptionOut]
    tools: list[AgentToolOptionOut]  # catalog rows with agents_allowed
    core_tools: list[str]  # locked KS core names — the UI renders, never defines them


class AgentOwnerOut(BaseModel):
    id: int
    email: str
    display_name: str | None


class AdminAgentOut(BaseModel):
    id: int
    name: str
    description: str | None
    schedule: ScheduleField | None
    enabled: bool
    admin_paused: bool
    status: AgentStatus
    owner: AgentOwnerOut
    last_run: LastRunOut | None
    created_at: UtcDateTime


class AdminAgentDetailOut(AdminAgentOut):
    prompt: str
    model_name: str | None  # display name; None → model missing (SET NULL)
    tools: list[AgentToolOptionOut]
    next_run_at: UtcDateTime | None
    owner_budget: BudgetOut  # the owner's weekly spend across all their agents


class AdminPauseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paused: bool


class AgentLimitsOut(BaseModel):
    iteration_cap: int
    max_concurrency: int


class RunStarted(BaseModel):
    run_id: int
