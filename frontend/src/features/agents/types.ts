/** Mirrors backend/src/achilles/agent_engine/schemas.py (wire contract). */

export type ScheduleSpec =
  | { type: "interval"; every_hours: number }
  | { type: "calendar"; cadence: "daily" | "weekly"; weekday?: number | null; time: string };

export type AgentStatus =
  | "active"
  | "disabled"
  | "admin_paused"
  | "budget_exceeded"
  | "model_missing";

/** Derived statuses the chip shows — the Status facet lists these, OR-combined.
 * Shared by My agents and admin/All agents. */
export const STATUS_VALUES: AgentStatus[] = [
  "active",
  "disabled",
  "admin_paused",
  "budget_exceeded",
  "model_missing",
];

/** Schedule facet: scheduled vs. manual runs. */
export const SCHEDULE_VALUES = ["scheduled", "manual"] as const;

export type RunState = "queued" | "running" | "succeeded" | "failed" | "skipped";

type RunReason = "budget_exceeded" | "already_running" | "iteration_cap" | "error" | "stale" | null;

export interface AgentRun {
  id: number;
  trigger: "manual" | "scheduled";
  state: RunState;
  reason: RunReason;
  output: string | null;
  tokens_used: number;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
  created_at: string;
}

interface LastRun {
  state: RunState;
  reason: RunReason;
  finished_at: string | null;
  duration_seconds: number | null;
  tokens_used: number;
}

export interface Agent {
  id: number;
  name: string;
  description: string | null;
  prompt: string;
  schedule: ScheduleSpec | null;
  model_id: number | null;
  enabled: boolean;
  admin_paused: boolean;
  status: AgentStatus;
  tool_ids: number[];
  /** Selected tools an admin has since disallowed for agents — shown disabled, never dropped. */
  disabled_tools: AgentToolOption[];
  next_run_at: string | null;
  last_run: LastRun | null;
  created_at: string;
}

interface Budget {
  used: number;
  limit: number | null;
  week_resets_at: string;
}

export interface AgentList {
  items: Agent[];
  budget: Budget;
}

interface AgentModelOption {
  id: number;
  display_name: string;
  is_default: boolean;
}

interface AgentToolOption {
  id: number;
  name: string;
}

export interface AgentOptions {
  models: AgentModelOption[];
  tools: AgentToolOption[];
  /** Locked KS core tool names — the backend owns the list. */
  core_tools: string[];
}

interface AgentOwner {
  id: number;
  email: string;
  display_name: string | null;
}

export interface AdminAgent {
  id: number;
  name: string;
  description: string | null;
  schedule: ScheduleSpec | null;
  enabled: boolean;
  admin_paused: boolean;
  status: AgentStatus;
  owner: AgentOwner;
  last_run: LastRun | null;
  created_at: string;
}

export interface AdminAgentDetail extends AdminAgent {
  prompt: string;
  model_name: string | null;
  tools: AgentToolOption[];
  next_run_at: string | null;
  owner_budget: Budget;
}

export interface AgentLimits {
  iteration_cap: number;
  max_concurrency: number;
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export interface AgentCreateBody {
  name: string;
  description?: string | null;
  prompt: string;
  schedule?: ScheduleSpec | null;
  model_id?: number | null;
  tool_ids?: number[];
}

export interface AgentPatchBody {
  name?: string;
  description?: string | null;
  prompt?: string;
  schedule?: ScheduleSpec | null;
  model_id?: number | null;
  enabled?: boolean;
  tool_ids?: number[];
}
