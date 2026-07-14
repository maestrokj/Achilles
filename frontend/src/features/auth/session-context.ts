import { createContext, useContext } from "react";

import type { SessionState } from "./session-store";

export const SessionContext = createContext<SessionState | null>(null);

export function useSession(): SessionState {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useSession must be used within SessionProvider");
  return session;
}
