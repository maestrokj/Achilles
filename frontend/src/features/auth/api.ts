/** Calls to backend auth routes (auth/routes/session.py, password.py). */

import { api } from "@/api/client";

import { clearSession, setSession, updateSessionUser } from "./session-store";
import type { SessionResponse } from "./types";

export interface LoginInput {
  email: string;
  password: string;
  rememberMe: boolean;
}

export interface SetupInput {
  email: string;
  fullName: string;
  password: string;
}

/** Anonymous first-run probe: is the platform still awaiting its Owner? */
export function getSetupStatus(): Promise<{ needs_setup: boolean }> {
  return api.get("auth/setup").json<{ needs_setup: boolean }>();
}

export async function setup(input: SetupInput): Promise<SessionResponse> {
  const data = await api
    .post("auth/setup", {
      json: { email: input.email, full_name: input.fullName, password: input.password },
    })
    .json<SessionResponse>();
  setSession(data.access_token, data.user);
  return data;
}

export async function login(input: LoginInput): Promise<SessionResponse> {
  const data = await api
    .post("auth/login", {
      json: {
        email: input.email,
        password: input.password,
        remember_me: input.rememberMe,
      },
    })
    .json<SessionResponse>();
  setSession(data.access_token, data.user);
  return data;
}

export async function logout(): Promise<void> {
  try {
    await api.post("auth/logout");
  } catch {
    // the server-side session is already gone — drop the local one regardless
  }
  clearSession("logout");
}

export async function logoutAll(): Promise<void> {
  try {
    await api.post("auth/logout-all");
  } catch {
    // same as logout: local cleanup must not depend on the server answer
  }
  clearSession("logout");
}

export async function changePassword(currentPassword: string, newPassword: string): Promise<void> {
  await api.post("auth/password/change", {
    json: { current_password: currentPassword, new_password: newPassword },
  });
  updateSessionUser({ must_change_password: false });
}

/** Anti-enumeration: the answer is uniform — the letter goes out via the worker. */
export async function forgotPassword(email: string): Promise<void> {
  await api.post("auth/password/forgot", { json: { email } });
}

export async function resetPassword(token: string, newPassword: string): Promise<void> {
  await api.post("auth/password/reset", { json: { token, new_password: newPassword } });
}

export async function acceptInvite(
  token: string,
  fullName: string,
  password: string,
): Promise<SessionResponse> {
  const data = await api
    .post(`invites/${token}/accept`, { json: { full_name: fullName, password } })
    .json<SessionResponse>();
  setSession(data.access_token, data.user);
  return data;
}
