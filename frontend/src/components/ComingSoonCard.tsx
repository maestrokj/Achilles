import { SparklesIcon } from "lucide-react";
import type { ComponentType } from "react";

import { InDevelopmentBadge } from "@/components/InDevelopmentBadge";
import { Card } from "@/components/ui/card";

/** Placeholder for a feature that has not shipped yet. A calm, muted card — a
 * neutral icon tile, an "in development" pill and a one-line preview — reads as
 * "not here yet" through its greyed tone, without going transparent. */
export function ComingSoonCard({
  title,
  note,
  icon: Icon = SparklesIcon,
}: {
  title: string;
  note: string;
  icon?: ComponentType<{ className?: string }>;
}) {
  return (
    <Card className="bg-muted/40 ring-border/70">
      <div className="flex items-start gap-3 px-(--card-spacing)">
        <span className="bg-muted text-muted-foreground ring-border flex size-9 shrink-0 items-center justify-center rounded-lg ring-1">
          <Icon className="size-4.5" />
        </span>
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <h3 className="font-heading text-foreground/80 text-base leading-snug font-medium">
            {title}
          </h3>
          <p className="text-muted-foreground text-sm">{note}</p>
        </div>
        <InDevelopmentBadge className="mt-0.5" />
      </div>
    </Card>
  );
}
