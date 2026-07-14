import { Link } from "react-router-dom";

import { sourceRefPath } from "./sourceRef";
import type { NotificationItem, Surface } from "./types";

/** The deep link tail of a notification row — "source · ref" routed to the
 * admin or personal screen. Renders nothing when the ref has no target. */
export function SourceRefLink({
  item,
  surface,
  className,
  onNavigate,
}: {
  item: Pick<NotificationItem, "source_ref" | "source">;
  surface: Surface;
  className?: string;
  onNavigate?: () => void;
}) {
  const refPath = sourceRefPath(item.source_ref, surface);
  if (!refPath || !item.source_ref) return null;
  return (
    <Link
      to={refPath}
      className={`text-muted-foreground hover:text-foreground text-xs underline underline-offset-4 ${className ?? ""}`}
      onClick={
        onNavigate
          ? (event) => {
              event.stopPropagation();
              onNavigate();
            }
          : undefined
      }
    >
      {item.source ? `${item.source} · ${item.source_ref}` : item.source_ref}
    </Link>
  );
}
