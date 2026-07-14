import type { ReactElement } from "react";

import { SessionContext } from "@/features/auth/session-context";
import type { SessionUser } from "@/features/auth/types";

import { renderWithProviders } from "./render";

function sessionUser(role: SessionUser["role"]): SessionUser {
  return {
    id: 1,
    email: "someone@acme.example",
    full_name: "Someone",
    role,
    status: "active",
    must_change_password: false,
    timezone: null,
    locale: null,
    date_format: null,
    last_login_at: null,
    created_at: "2026-01-01T00:00:00Z",
  };
}

/** Render under an authenticated session with the given role. */
export function renderAs(
  role: SessionUser["role"],
  ui: ReactElement,
  options?: { route?: string; user?: Partial<SessionUser> },
) {
  return renderWithProviders(
    <SessionContext.Provider
      value={{
        status: "authenticated",
        user: { ...sessionUser(role), ...options?.user },
        expired: false,
      }}
    >
      {ui}
    </SessionContext.Provider>,
    { route: options?.route ?? "/" },
  );
}

/** Render under an anonymous session (public screens: login, setup). */
export function renderAnon(ui: ReactElement, options?: { route?: string }) {
  return renderWithProviders(
    <SessionContext.Provider value={{ status: "anonymous", user: null, expired: false }}>
      {ui}
    </SessionContext.Provider>,
    { route: options?.route ?? "/" },
  );
}
