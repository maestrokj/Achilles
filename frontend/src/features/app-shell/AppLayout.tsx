import { Navigate, Outlet, useLocation } from "react-router-dom";

import { ChangePasswordPage } from "@/features/auth/ChangePasswordPage";
import { useSession } from "@/features/auth/session-context";
import { SESSION_EXPIRED_REASON } from "@/features/auth/session-store";

import { Sidebar } from "./Sidebar";

/** End-user shell (chat surface): authentication-only gate — every role is
 * welcome here, member included. Claude-style sidebar + full-height <Outlet/>. */
export function AppLayout() {
  const session = useSession();
  const location = useLocation();

  if (session.status !== "authenticated") {
    const params = new URLSearchParams({ returnTo: location.pathname + location.search });
    if (session.expired) params.set("reason", SESSION_EXPIRED_REASON);
    return <Navigate to={`/login?${params.toString()}`} replace />;
  }
  if (session.user.must_change_password) return <ChangePasswordPage />;

  return (
    <div className="bg-background text-foreground flex h-dvh">
      <Sidebar user={session.user} />
      <main className="min-w-0 flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  );
}
