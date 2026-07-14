import { useEffect, useRef, useSyncExternalStore, type ReactNode } from "react";

import { refreshSession } from "@/api/client";
import { SplashScreen } from "@/features/app-shell/SplashScreen";

import { SessionContext } from "./session-context";
import { getSessionState, subscribeSession } from "./session-store";

/** Restores identity with one silent refresh on app start (no /me endpoint);
 * the app renders only after that attempt settles. */
export function SessionProvider({ children }: { children: ReactNode }) {
  const session = useSyncExternalStore(subscribeSession, getSessionState);
  const bootstrapped = useRef(false);

  useEffect(() => {
    if (bootstrapped.current) return;
    bootstrapped.current = true;
    void refreshSession();
  }, []);

  if (session.status === "loading") return <SplashScreen />;

  return <SessionContext.Provider value={session}>{children}</SessionContext.Provider>;
}
