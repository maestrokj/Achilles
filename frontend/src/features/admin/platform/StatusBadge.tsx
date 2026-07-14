import { CircleCheckIcon, CircleDashedIcon, CircleXIcon, LoaderCircleIcon } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

type StatusState = "ok" | "failed" | "untested" | "testing";

const STATUS_STYLE: Record<StatusState, { icon: LucideIcon; color: string; chip: string }> = {
  ok: { icon: CircleCheckIcon, color: "text-success", chip: "border-success/30 bg-success/10" },
  failed: {
    icon: CircleXIcon,
    color: "text-destructive",
    chip: "border-destructive/30 bg-destructive/10",
  },
  untested: {
    icon: CircleDashedIcon,
    color: "text-muted-foreground",
    chip: "border-border bg-muted",
  },
  testing: {
    icon: LoaderCircleIcon,
    color: "text-muted-foreground",
    chip: "border-border bg-muted",
  },
};

/** The last-test verdict of an integration card in one shared visual language:
 * a colored status icon (green ok · red failed · muted never-tested) that names
 * the state on hover. While a probe is in flight the icon becomes a spinner so
 * the admin sees the check is running. With a handle — the Telegram @bot — the
 * icon and handle sit together in a framed chip in the matching color; without
 * one it is a bare icon. Labels come in resolved; each card keeps its own i18n
 * namespace, and the in-flight label is shared across cards. */
export function StatusBadge({
  ok,
  labels,
  handle,
  pending = false,
  pendingLabel,
}: {
  ok: boolean | null;
  labels: { ok: string; failed: string; untested: string };
  /** Optional text framed beside the icon in the status color (Telegram @bot). */
  handle?: string;
  /** While true the badge shows a spinner instead of the last-test verdict. */
  pending?: boolean;
  /** Shared "testing connection…" label, shown on hover while pending. */
  pendingLabel?: string;
}) {
  const state: StatusState = pending ? "testing" : ok === null ? "untested" : ok ? "ok" : "failed";
  const { icon: Icon, color, chip } = STATUS_STYLE[state];
  const label = state === "testing" ? (pendingLabel ?? labels.untested) : labels[state];

  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <span
            className={cn(
              "inline-flex items-center gap-1.5",
              color,
              handle && cn("rounded-md border px-2 py-0.5", chip),
            )}
          />
        }
      >
        <Icon
          className={cn("size-4 shrink-0", state === "testing" && "animate-spin")}
          role="img"
          aria-label={label}
        />
        {handle && <span className="text-sm font-medium">{handle}</span>}
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  );
}
