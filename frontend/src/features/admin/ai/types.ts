/** Contracts of /admin/ai/* and /admin/usage (ai_foundation routes). */

import type { OffsetPage } from "@/api/lists";

export type CheckStatus = "active" | "error" | "unchecked";

export interface Provider {
  id: number;
  name: string;
  kind: "cloud" | "local" | "platform";
  adapter: string;
  base_url: string | null;
  api_key_mask: string | null;
  is_system: boolean;
  status: CheckStatus;
  last_check_at: string | null;
}

export type ModelType = "chat" | "embedding";

export interface AiModel {
  id: number;
  provider_id: number;
  model_id: string;
  display_name: string;
  model_type: ModelType;
  origin: string;
  is_enabled: boolean;
  price_input: string | null;
  price_output: string | null;
  meta: Record<string, unknown> | null;
}

/** One entry of a chat/agent allow-list: a catalogue model with its pause flag.
 * `id` is the ai_models id; `is_enabled` false = kept in the list but off the
 * surface (paused), distinct from removal which drops the entry entirely. */
export interface ModelListItem {
  id: number;
  is_enabled: boolean;
}

export interface ModelList {
  items: ModelListItem[];
  default: number | null;
}

export interface Assignments {
  harvester_embedding: number | null;
  chat_models: ModelList;
  agent_models: ModelList;
  /** Width the knowledge base column is provisioned to (halfvec(N)); the embedder
   * picker gates options against it. Backend is the source of truth — never hardcoded. */
  embedding_dim: number;
}

/** Live phase of the assigned embedding model (GET /admin/ai/embedder).
 * `unreachable` — the runtime gave no answer; `external` — a cloud embedder
 * with no load phase to report. */
type EmbedderRuntimeState =
  | "not_loaded"
  | "loading"
  | "ready"
  | "error"
  | "unreachable"
  | "external";

export interface EmbedderStatus {
  assigned: { model_pk: number; model_id: string; display_name: string } | null;
  runtime: { state: EmbedderRuntimeState; error: string | null } | null;
}

export interface Discovery {
  models: { model_id: string; display_name: string | null; model_type?: ModelType }[];
}

export interface Tool {
  id: number | null;
  name: string;
  source: string;
  access: string;
  config: Record<string, unknown> | null;
  credential_is_set: boolean;
  needs_credential: boolean;
  chat_enabled: boolean;
  agents_allowed: boolean;
  status: CheckStatus;
  last_check_at: string | null;
  parameters: Record<string, unknown>;
}

export interface PromptBlock {
  text: string;
  is_default: boolean;
}

export interface Prompt {
  safety: PromptBlock;
  org: PromptBlock;
}

interface WindowTotal {
  tokens: number;
  cost: string | null;
}

interface UsageLimits {
  agent_weekly_token_budget: number | null;
  chat_weekly_token_budget: number | null;
  ai_monthly_budget: string | null;
  ai_budget_alert_enabled: boolean;
}

interface UserSpend {
  user_id: number;
  full_name: string;
  email: string;
  role: string;
  agent_tokens: number;
  chat_tokens: number;
  total_tokens: number;
  agent_over_limit: boolean;
  chat_over_limit: boolean;
}

export interface ModelSpend {
  display_name: string | null;
  provider_name: string | null;
  function: string;
  request_count: number;
  input_tokens: number;
  output_tokens: number;
  cost: string | null;
}

export interface Usage {
  totals: { week: WindowTotal; month: WindowTotal; year: WindowTotal };
  limits: UsageLimits;
  by_user: OffsetPage<UserSpend>;
  by_model: ModelSpend[];
}

export interface UserUsage {
  user_id: number;
  full_name: string;
  email: string;
  agent_tokens: number;
  chat_tokens: number;
  limits: UsageLimits;
  agents: { agent_id: number; name: string; model: string | null; runs: number; tokens: number }[];
  chat: { model: string; messages: number; tokens: number }[];
}
