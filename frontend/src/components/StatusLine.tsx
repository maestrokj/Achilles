import type { LucideIcon } from "lucide-react";
import type * as React from "react";

import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";

export type StatusTone = "success" | "destructive" | "primary" | "warning" | "muted";

const STATUS_TONE: Record<StatusTone, string> = {
  success: "bg-success/10 text-success",
  destructive: "bg-destructive/10 text-destructive",
  primary: "bg-primary/10 text-primary",
  warning: "bg-warning/10 text-warning",
  muted: "bg-muted text-muted-foreground",
};

/** Icon disc + a bold state line and a muted meta line — the shared body of the
 * curation and backup status cards. Optional children (a progress bar, a hint)
 * sit below. */
export function StatusLine({
  tone,
  icon: Icon,
  spinning,
  primary,
  meta,
  children,
}: {
  tone: StatusTone;
  icon: LucideIcon;
  spinning?: boolean;
  primary: string;
  meta?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex items-center gap-3">
        <span
          className={cn(
            "flex size-8 shrink-0 items-center justify-center rounded-lg",
            STATUS_TONE[tone],
          )}
        >
          {spinning ? <Spinner className="size-4" /> : <Icon className="size-4" />}
        </span>
        <div className="flex min-w-0 flex-col gap-0.5">
          <span className="text-sm leading-snug font-medium">{primary}</span>
          {meta && <span className="text-muted-foreground text-xs tabular-nums">{meta}</span>}
        </div>
      </div>
      {children}
    </div>
  );
}
