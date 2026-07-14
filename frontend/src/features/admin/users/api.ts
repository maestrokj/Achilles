/** Calls to the user-management backend (auth admin routes + KS identity admin). */

import { PER_PAGE_CHOICES } from "@/components/list-controls/useListState";

import { api } from "@/api/client";
import { qs, type ListQuery, type OffsetPage } from "@/api/lists";

import type { ApiKey, ApiKeyCreated } from "@/features/auth/api-keys";

import type {
  AdminUser,
  AdminUserDetail,
  AdminUserPatch,
  BulkReport,
  Invite,
  MappingCandidate,
  MappingLink,
  MappingPage,
} from "./types";

export const usersKeys = {
  list: (query: ListQuery) => ["admin", "users", "list", query] as const,
  detail: (id: number) => ["admin", "users", "detail", id] as const,
  keys: (userId: number) => ["admin", "users", "keys", userId] as const,
  invites: (query: ListQuery) => ["admin", "invites", query] as const,
  mapping: (query: ListQuery) => ["admin", "identity-mapping", query] as const,
  candidates: (sourceId: number, q: string) =>
    ["admin", "identity-candidates", sourceId, q] as const,
};

export function listUsers(searchParams: ListQuery): Promise<OffsetPage<AdminUser>> {
  return api.get("admin/users", { searchParams: qs(searchParams) }).json<OffsetPage<AdminUser>>();
}

/** Typeahead over the user directory: request the smallest allowed backend page
 *  (per_page ∈ {10,25,50,100}) and render only the head — the dropdown never
 *  shows a full page of results. */
export const USER_SUGGEST_PAGE_SIZE = PER_PAGE_CHOICES[0];
export const USER_SUGGEST_LIMIT = 6;

/** Download the current list (search + facets applied) as CSV or JSON. The
 * request carries the Bearer token, so we pull a blob and save it client-side. */
export function exportUsers(searchParams: ListQuery, format: "csv" | "json"): Promise<Blob> {
  return api.get("admin/users/export", { searchParams: qs({ ...searchParams, format }) }).blob();
}

export function getUser(id: number): Promise<AdminUserDetail> {
  return api.get(`admin/users/${String(id)}`).json<AdminUserDetail>();
}

export function patchUser(id: number, body: AdminUserPatch): Promise<AdminUser> {
  return api.patch(`admin/users/${String(id)}`, { json: body }).json<AdminUser>();
}

export async function deleteUser(id: number): Promise<void> {
  await api.delete(`admin/users/${String(id)}`);
}

export interface AdminResetResult {
  /** `link`: a 1h reset letter was queued; `temp_password`: the SMTP-less fallback. */
  mode: "link" | "temp_password";
  temp_password: string | null;
}

export function resetPassword(id: number): Promise<AdminResetResult> {
  return api.post(`admin/users/${String(id)}/reset-password`).json<AdminResetResult>();
}

export async function terminateSessions(id: number): Promise<void> {
  await api.post(`admin/users/${String(id)}/terminate-sessions`);
}

export function listInvites(searchParams: ListQuery): Promise<OffsetPage<Invite>> {
  return api.get("invites", { searchParams: qs(searchParams) }).json<OffsetPage<Invite>>();
}

export function createInvite(body: { email: string; role: string }): Promise<Invite> {
  return api.post("invites", { json: body }).json<Invite>();
}

/** Bulk invite CSV (`email[,role]`). `dryRun` returns the same per-row report
 * without creating anything — the preview step of the import wizard. */
export function bulkInvite(options: {
  file: File;
  dryRun: boolean;
  defaultRole: string;
}): Promise<BulkReport> {
  const body = new FormData();
  body.append("file", options.file);
  return api
    .post("invites/bulk", {
      body,
      searchParams: { dry_run: options.dryRun, default_role: options.defaultRole },
    })
    .json<BulkReport>();
}

export function resendInvite(id: number): Promise<Invite> {
  return api.post(`invites/${String(id)}/resend`).json<Invite>();
}

export async function revokeInvite(id: number): Promise<void> {
  await api.delete(`invites/${String(id)}`);
}

export function mappingMatrix(searchParams: ListQuery): Promise<MappingPage> {
  return api.get("admin/identity-mapping", { searchParams: qs(searchParams) }).json<MappingPage>();
}

export function mappingCandidates(
  sourceId: number,
  q: string,
): Promise<{ items: MappingCandidate[] }> {
  const searchParams: ListQuery = q ? { source_id: sourceId, q } : { source_id: sourceId };
  return api
    .get("admin/identity-mapping/candidates", { searchParams: qs(searchParams) })
    .json<{ items: MappingCandidate[] }>();
}

export function linkPrincipal(body: {
  principal_id: number;
  user_id: number;
}): Promise<MappingLink> {
  return api.post("admin/identity-mapping/link", { json: body }).json<MappingLink>();
}

export async function unlinkPrincipal(principalId: number): Promise<void> {
  await api.post("admin/identity-mapping/unlink", { json: { principal_id: principalId } });
}

export function listUserKeys(userId: number): Promise<{ items: ApiKey[] }> {
  return api.get("api-keys", { searchParams: { user_id: userId } }).json<{ items: ApiKey[] }>();
}

export function createKey(body: {
  user_id?: number;
  expires_in_days?: number;
  name?: string;
}): Promise<ApiKeyCreated> {
  return api.post("api-keys", { json: body }).json<ApiKeyCreated>();
}

export async function revokeKey(id: number): Promise<void> {
  await api.delete(`api-keys/${String(id)}`);
}
