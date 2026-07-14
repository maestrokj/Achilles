/** In-memory session: the access token never touches storage, identity is
 * restored via the refresh cookie on app start (there is no /me endpoint). */

import type { SessionUser } from "./types";

/** Query-param value the login screen reads to explain an involuntary drop. */
export const SESSION_EXPIRED_REASON = "session-expired";

export type SessionState =
  | { status: "loading"; user: null; expired: false }
  | { status: "authenticated"; user: SessionUser; expired: false }
  | { status: "anonymous"; user: null; expired: boolean };

let accessToken: string | null = null;
let state: SessionState = { status: "loading", user: null, expired: false };
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

export function subscribeSession(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getSessionState(): SessionState {
  return state;
}

export function getAccessToken(): string | null {
  return accessToken;
}

export function setSession(token: string, user: SessionUser): void {
  accessToken = token;
  state = { status: "authenticated", user, expired: false };
  emit();
}

export function updateSessionUser(patch: Partial<SessionUser>): void {
  if (state.status !== "authenticated") return;
  state = { ...state, user: { ...state.user, ...patch } };
  emit();
}

/** "expired" marks an involuntary drop (failed refresh of a live session) so the
 * login screen can explain it; a plain logout stays silent. */
export function clearSession(reason: "expired" | "logout"): void {
  const wasAuthenticated = state.status === "authenticated";
  accessToken = null;
  state = { status: "anonymous", user: null, expired: reason === "expired" && wasAuthenticated };
  emit();
}
