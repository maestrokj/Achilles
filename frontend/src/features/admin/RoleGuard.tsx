import { Navigate, Outlet, useLocation } from "react-router-dom";

import { ChangePasswordPage } from "@/features/auth/ChangePasswordPage";
import { isMember } from "@/features/auth/roles";
import { useSession } from "@/features/auth/session-context";
import { SESSION_EXPIRED_REASON } from "@/features/auth/session-store";
import { homePath } from "@/features/app-shell/home";

/** Gate on /admin: anonymous → /login (with a way back), forced password change
 * blocks everything, a member has no business in the admin panel and is sent to
 * their own surface (chat) rather than a dead-end 403; owner/admin fall through. */
export function RoleGuard() {
  const session = useSession();
  const location = useLocation();

  if (session.status !== "authenticated") {
    const params = new URLSearchParams({ returnTo: location.pathname + location.search });
    if (session.expired) params.set("reason", SESSION_EXPIRED_REASON);
    return <Navigate to={`/login?${params.toString()}`} replace />;
  }
  if (session.user.must_change_password) return <ChangePasswordPage />;
  if (isMember(session.user.role)) return <Navigate to={homePath(session.user.role)} replace />;
  return <Outlet />;
}
