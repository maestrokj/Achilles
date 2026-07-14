/** ky instance for the versioned API: Bearer from the in-memory store, cookies
 * included (refresh travels httpOnly), 401 → single shared refresh → retry. */

import ky, { HTTPError } from "ky";

import { PROBLEM_CODES, responseProblem } from "@/api/problems";
import { API_V1_URL } from "@/constants/api";
import { setMaintenanceActive } from "@/features/app-shell/maintenance-store";
import {
  clearSession,
  getAccessToken,
  getSessionState,
  setSession,
} from "@/features/auth/session-store";
import type { SessionResponse } from "@/features/auth/types";

/** No auth hooks — used for the refresh call itself. */
const bare = ky.create({ prefix: API_V1_URL, credentials: "include" });

let refreshPromise: Promise<boolean> | null = null;

/** One refresh in flight at a time; concurrent 401s share the same attempt. */
export function refreshSession(): Promise<boolean> {
  refreshPromise ??= bare
    .post("auth/refresh")
    .json<SessionResponse>()
    .then((data) => {
      setSession(data.access_token, data.user);
      return true;
    })
    .catch((error: unknown) => {
      // Only a 401 means the refresh cookie itself is dead → real expiry. A
      // network blip or 5xx leaves a live session intact so a retry can
      // recover — but at bootstrap there is nothing to keep: settle to
      // anonymous, or the splash screen would hang forever (e.g. a 403 from
      // the origin barrier).
      if (error instanceof HTTPError && error.response.status === 401) {
        clearSession("expired");
      } else if (getSessionState().status === "loading") {
        clearSession("logout");
      }
      return false;
    })
    .finally(() => {
      refreshPromise = null;
    });
  return refreshPromise;
}

/** Only these 401 codes mean "the access token went stale" — anything else
 * (e.g. INVALID_CREDENTIALS from login or password change) must reach the caller. */
const REFRESHABLE_CODES = new Set<string>([
  PROBLEM_CODES.TOKEN_EXPIRED,
  PROBLEM_CODES.TOKEN_INVALID,
]);

export const api = bare.extend({
  hooks: {
    beforeRequest: [
      ({ request }) => {
        const token = getAccessToken();
        if (token) request.headers.set("Authorization", `Bearer ${token}`);
      },
    ],
    afterResponse: [
      // Org maintenance: raise the flag on 503 MAINTENANCE, lower it on the
      // next success — the gate component shows/hides the full-screen stub.
      async ({ response }) => {
        if (response.status === 503) {
          const problem = await responseProblem(response);
          if (problem?.code === PROBLEM_CODES.MAINTENANCE) setMaintenanceActive(true);
        } else if (response.ok) {
          setMaintenanceActive(false);
        }
      },
      async ({ request, response, retryCount }) => {
        if (response.status !== 401 || retryCount > 0) return;
        const problem = await responseProblem(response);
        if (!problem || !REFRESHABLE_CODES.has(problem.code)) return;
        if (!(await refreshSession())) return;
        const token = getAccessToken();
        if (!token) return;
        const headers = new Headers(request.headers);
        headers.set("Authorization", `Bearer ${token}`);
        return ky.retry({
          request: new Request(request, { headers }),
          code: "TOKEN_REFRESHED",
        });
      },
    ],
  },
});
