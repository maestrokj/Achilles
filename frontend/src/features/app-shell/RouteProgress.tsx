import { useNavigation } from "react-router-dom";

/** The "bar at the top" of loading-states.html — a thin indeterminate strip
 * under the header while the router is navigating. Fires whenever the target
 * route's lazy chunk is still downloading (useNavigation → "loading"); stays
 * idle for already-loaded chunks and synchronous element routes. */
export function RouteProgress() {
  const navigation = useNavigation();
  if (navigation.state === "idle") return null;

  return (
    <div className="fixed inset-x-0 top-0 z-50 h-0.5 overflow-hidden" role="progressbar">
      <div className="bg-primary animate-route-progress h-full w-1/3" />
    </div>
  );
}
