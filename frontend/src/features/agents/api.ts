/** Calls to backend agent routes (agent_engine/router.py). */

import { api } from "@/api/client";
import { qs, type ListQuery, type OffsetPage } from "@/api/lists";

import type {
  AdminAgent,
  AdminAgentDetail,
  Agent,
  AgentCreateBody,
  AgentLimits,
  AgentList,
  AgentOptions,
  AgentPatchBody,
  AgentRun,
  Page,
} from "./types";

export const agentsQueryKeys = {
  list: ["agents", "list"] as const,
  agent: (id: number) => ["agents", "agent", id] as const,
  runs: (id: number) => ["agents", "runs", id] as const,
  options: ["agents", "options"] as const,
  adminList: (query: ListQuery) => ["agents", "admin", "list", query] as const,
  adminAgent: (id: number) => ["agents", "admin", "agent", id] as const,
  adminRuns: (id: number) => ["agents", "admin", "runs", id] as const,
  limits: ["agents", "admin", "limits"] as const,
};

export function listAgents(): Promise<AgentList> {
  return api.get("agents").json<AgentList>();
}

export function getAgent(id: number): Promise<Agent> {
  return api.get(`agents/${String(id)}`).json<Agent>();
}

export function getAgentOptions(): Promise<AgentOptions> {
  return api.get("agents/options").json<AgentOptions>();
}

export function createAgent(body: AgentCreateBody): Promise<Agent> {
  return api.post("agents", { json: body }).json<Agent>();
}

export function patchAgent(id: number, body: AgentPatchBody): Promise<Agent> {
  return api.patch(`agents/${String(id)}`, { json: body }).json<Agent>();
}

export async function deleteAgent(id: number): Promise<void> {
  await api.delete(`agents/${String(id)}`);
}

export function runAgent(id: number): Promise<{ run_id: number }> {
  return api.post(`agents/${String(id)}/run`).json<{ run_id: number }>();
}

export function listRuns(id: number, cursor?: string | null): Promise<Page<AgentRun>> {
  const searchParams = cursor ? { cursor } : undefined;
  return api.get(`agents/${String(id)}/runs`, { searchParams }).json<Page<AgentRun>>();
}

// --- Admin surface ---

export interface AdminAgentsParams {
  q?: string;
  status?: string[];
  scheduled?: boolean;
  page?: number;
  per_page?: number;
}

export function adminListAgents(params: AdminAgentsParams): Promise<OffsetPage<AdminAgent>> {
  const query: ListQuery = {};
  if (params.q) query.q = params.q;
  // Statuses ride as repeated ?status= params — the backend ORs them.
  if (params.status && params.status.length > 0) query.status = params.status;
  if (params.scheduled !== undefined) query.scheduled = String(params.scheduled);
  if (params.page && params.page > 1) query.page = params.page;
  if (params.per_page) query.per_page = params.per_page;
  return api.get("admin/agents", { searchParams: qs(query) }).json<OffsetPage<AdminAgent>>();
}

export function adminGetAgent(id: number): Promise<AdminAgentDetail> {
  return api.get(`admin/agents/${String(id)}`).json<AdminAgentDetail>();
}

export function adminListRuns(id: number, cursor?: string | null): Promise<Page<AgentRun>> {
  const searchParams = cursor ? { cursor } : undefined;
  return api.get(`admin/agents/${String(id)}/runs`, { searchParams }).json<Page<AgentRun>>();
}

export function adminSetPause(id: number, paused: boolean): Promise<AdminAgentDetail> {
  return api
    .patch(`admin/agents/${String(id)}/pause`, { json: { paused } })
    .json<AdminAgentDetail>();
}

export function getAgentLimits(): Promise<AgentLimits> {
  return api.get("admin/agent-limits").json<AgentLimits>();
}

export function patchAgentLimits(body: Partial<AgentLimits>): Promise<AgentLimits> {
  return api.patch("admin/agent-limits", { json: body }).json<AgentLimits>();
}
