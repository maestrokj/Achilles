/** Calls to the AI Foundation admin backend (registry / tools / prompt / usage). */

import { api } from "@/api/client";
import { qs, type ListQuery } from "@/api/lists";

import type {
  AiModel,
  Assignments,
  CheckStatus,
  Discovery,
  EmbedderStatus,
  ModelType,
  Prompt,
  Provider,
  Tool,
  Usage,
  UserUsage,
} from "./types";

interface CheckVerdict {
  status: CheckStatus;
  last_check_at: string;
}

export const aiKeys = {
  providers: ["admin", "ai", "providers"] as const,
  models: ["admin", "ai", "models"] as const,
  assignments: ["admin", "ai", "assignments"] as const,
  embedder: ["admin", "ai", "embedder"] as const,
  discovery: (providerId: number) => ["admin", "ai", "discovery", providerId] as const,
  tools: ["admin", "ai", "tools"] as const,
  prompt: ["admin", "ai", "prompt"] as const,
  usage: (query: ListQuery) => ["admin", "ai", "usage", query] as const,
  userUsage: (userId: number, window: string) =>
    ["admin", "ai", "usage", "user", userId, window] as const,
};

export function listProviders(): Promise<Provider[]> {
  return api.get("admin/ai/providers").json<Provider[]>();
}

export function createProvider(body: {
  name: string;
  kind: string;
  adapter: string;
  base_url?: string | null;
  api_key?: string | null;
}): Promise<Provider> {
  return api.post("admin/ai/providers", { json: body }).json<Provider>();
}

export function patchProvider(
  id: number,
  body: { name?: string; base_url?: string | null; api_key?: string | null },
): Promise<Provider> {
  return api.patch(`admin/ai/providers/${String(id)}`, { json: body }).json<Provider>();
}

export async function deleteProvider(id: number): Promise<void> {
  await api.delete(`admin/ai/providers/${String(id)}`);
}

export function checkProvider(id: number): Promise<CheckVerdict> {
  return api.post(`admin/ai/providers/${String(id)}/check`).json<CheckVerdict>();
}

/** Probe a provider config before it exists — the connection fields only
 * (backend ProviderCheckConfig forbids extras; `name` is not part of a probe). */
export function checkProviderConfig(body: {
  kind: string;
  adapter: string;
  base_url?: string | null;
  api_key?: string | null;
}): Promise<CheckVerdict> {
  return api.post("admin/ai/providers/check-config", { json: body }).json<CheckVerdict>();
}

export function providerDiscovery(id: number): Promise<Discovery> {
  return api.get(`admin/ai/providers/${String(id)}/discovery`).json<Discovery>();
}

export function listModels(): Promise<AiModel[]> {
  return api.get("admin/ai/models").json<AiModel[]>();
}

export function createModel(body: {
  provider_id: number;
  model_id: string;
  display_name?: string;
  model_type: ModelType;
  origin?: "discovered" | "manual";
  // $ per 1M tokens, as decimal strings; omitted → stored NULL (usage stays
  // token-only until a price is set).
  price_input?: string | null;
  price_output?: string | null;
  // Model intrinsics — for embedding models, { embedding_dim } (discovery doesn't
  // report it, so it's declared here). Merged into the stored meta on the backend.
  meta?: Record<string, unknown> | null;
}): Promise<AiModel> {
  return api.post("admin/ai/models", { json: body }).json<AiModel>();
}

export function patchModel(
  id: number,
  body: {
    display_name?: string;
    is_enabled?: boolean;
    model_type?: ModelType;
    // $ per 1M tokens, as decimal strings; null clears the price (cost stops
    // counting). Omitted → the backend leaves the stored value untouched.
    price_input?: string | null;
    price_output?: string | null;
    // Partial intrinsics patch — merged into stored meta (other keys preserved).
    meta?: Record<string, unknown> | null;
  },
): Promise<AiModel> {
  return api.patch(`admin/ai/models/${String(id)}`, { json: body }).json<AiModel>();
}

export async function deleteModel(id: number): Promise<void> {
  await api.delete(`admin/ai/models/${String(id)}`);
}

export function getAssignments(): Promise<Assignments> {
  return api.get("admin/ai/assignments").json<Assignments>();
}

export function getEmbedderStatus(): Promise<EmbedderStatus> {
  return api.get("admin/ai/embedder").json<EmbedderStatus>();
}

export function patchAssignments(body: Partial<Assignments>): Promise<Assignments> {
  return api.patch("admin/ai/assignments", { json: body }).json<Assignments>();
}

export function listTools(): Promise<Tool[]> {
  return api.get("admin/ai/tools").json<Tool[]>();
}

/** Materialize a registered tool type into an instance row (id was null). */
export function createTool(body: {
  name: string;
  config?: Record<string, unknown>;
  credential?: string;
}): Promise<Tool> {
  return api.post("admin/ai/tools", { json: body }).json<Tool>();
}

export function patchTool(
  id: number,
  body: {
    chat_enabled?: boolean;
    agents_allowed?: boolean;
    config?: Record<string, unknown>;
    credential?: string;
  },
): Promise<Tool> {
  return api.patch(`admin/ai/tools/${String(id)}`, { json: body }).json<Tool>();
}

export function checkTool(id: number): Promise<CheckVerdict> {
  return api.post(`admin/ai/tools/${String(id)}/check`).json<CheckVerdict>();
}

export function getPrompt(): Promise<Prompt> {
  return api.get("admin/ai-prompt").json<Prompt>();
}

export function patchPrompt(body: {
  safety_text?: string | null;
  org_text?: string | null;
}): Promise<Prompt> {
  return api.patch("admin/ai-prompt", { json: body }).json<Prompt>();
}

export function getUsage(query: ListQuery): Promise<Usage> {
  return api.get("admin/usage", { searchParams: qs(query) }).json<Usage>();
}

export function getUserUsage(userId: number, window: string): Promise<UserUsage> {
  return api.get(`admin/usage/${String(userId)}`, { searchParams: { window } }).json<UserUsage>();
}
