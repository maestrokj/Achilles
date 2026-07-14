/** Self-service calls of the personal account screen (auth routes with no
 * user_id → the caller's own keys, sessions, profile, messenger link). */

import { api } from "@/api/client";
import { expiryPayload } from "@/features/auth/api-keys";
import type { ApiKey, ApiKeyCreated, ApiKeyExpiry } from "@/features/auth/api-keys";
import type { SessionUser } from "@/features/auth/types";

export const accountKeys = {
  apiKeys: ["account", "api-keys"] as const,
  sessions: ["account", "sessions"] as const,
  me: ["account", "me"] as const,
};

export function listMyKeys(): Promise<{ items: ApiKey[] }> {
  return api.get("api-keys").json<{ items: ApiKey[] }>();
}

export function createMyKey(expiry: ApiKeyExpiry, name?: string): Promise<ApiKeyCreated> {
  return api.post("api-keys", { json: { ...expiryPayload(expiry), name } }).json<ApiKeyCreated>();
}

export function renameMyKey(id: number, name: string | null): Promise<ApiKey> {
  return api.patch(`api-keys/${String(id)}`, { json: { name } }).json<ApiKey>();
}

export async function revokeMyKey(id: number): Promise<void> {
  await api.delete(`api-keys/${String(id)}`);
}

// --- Active device sessions (auth/routes/session.py) ---

export interface SessionInfo {
  id: string;
  user_agent: string | null;
  ip: string | null;
  created_at: string;
  is_current: boolean;
}

export function listSessions(): Promise<{ items: SessionInfo[] }> {
  return api.get("auth/sessions").json<{ items: SessionInfo[] }>();
}

export async function revokeSession(id: string): Promise<void> {
  await api.delete(`auth/sessions/${id}`);
}

export async function revokeOtherSessions(): Promise<void> {
  await api.post("auth/sessions/revoke-others");
}

// --- Profile: read with catalogues, edit name and region (auth/routes/profile.py) ---

export interface MeResponse {
  user: SessionUser;
  locale_choices: string[];
  date_format_choices: string[];
}

/** Partial: an absent field stays as it is, an explicit null clears the personal
 * override so the org default applies again (auth/routes/profile.py). */
export interface ProfilePatch {
  full_name?: string;
  timezone?: string | null;
  locale?: string | null;
  date_format?: string | null;
}

export function getMe(): Promise<MeResponse> {
  return api.get("auth/me").json<MeResponse>();
}

export function updateProfile(patch: ProfilePatch): Promise<SessionUser> {
  return api.patch("auth/me", { json: patch }).json<SessionUser>();
}

// --- Messenger linking: issue a short-lived code (auth/routes/link.py) ---

export interface LinkCode {
  code: string;
  expires_in_seconds: number;
}

export function issueLinkCode(platform: string): Promise<LinkCode> {
  return api.post(`link/${platform}`).json<LinkCode>();
}
