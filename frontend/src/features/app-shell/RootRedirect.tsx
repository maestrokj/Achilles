import { useQuery } from "@tanstack/react-query";
import { Navigate } from "react-router-dom";

import { getSetupStatus } from "@/features/auth/api";
import { useSession } from "@/features/auth/session-context";

import { homePath } from "./home";

/** "/" sends a signed-in user to their surface. An anonymous visitor goes to the
 * login form — unless the platform has no Owner yet, when the first-run wizard
 * takes over (setup-wizard.html). See auth-security/_workzone/routing.html#entry-gate. */
export function RootRedirect() {
  const session = useSession();
  const authenticated = session.status === "authenticated";
  const setup = useQuery({
    queryKey: ["setup-status"],
    queryFn: getSetupStatus,
    enabled: !authenticated,
  });

  if (authenticated) {
    return <Navigate to={homePath(session.user.role)} replace />;
  }
  // Hold the render until the first-run probe resolves — otherwise the login
  // form flashes for a round-trip before a no-Owner platform jumps to /setup.
  if (setup.isPending) {
    return null;
  }
  if (setup.data?.needs_setup) {
    return <Navigate to="/setup" replace />;
  }
  return <Navigate to="/login" replace />;
}
