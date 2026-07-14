import { Outlet } from "react-router-dom";

import { RouteProgress } from "./RouteProgress";

/** Pathless root layout wrapping every route: mounts the route-transition
 * progress overlay once (inside the data router, so useNavigation works) for
 * all surfaces — including the top-level auth screens that have no shell. */
export function RootShell() {
  return (
    <>
      <RouteProgress />
      <Outlet />
    </>
  );
}
