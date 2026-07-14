import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

import type { LucideIcon } from "lucide-react";

/** One settings section on the Platform screen: a soft icon tile, a title and a
 * quiet one-line subtitle, with an optional status slot on the right — the shared
 * frame that keeps every section (org, sessions, integrations, messengers) reading
 * as one page. Mirrors the AI-prompt card language. */
export function SectionCard({
  id,
  icon: Icon,
  title,
  subtitle,
  aside,
  children,
}: {
  /** Anchor id for a `#hash` deep-link — also earns a scroll offset. */
  id?: string;
  icon: LucideIcon;
  title: string;
  subtitle?: string;
  aside?: React.ReactNode;
  /** Section body. Omit for a header-only card (e.g. a bare toggle row). */
  children?: React.ReactNode;
}) {
  return (
    <Card id={id} className={cn("gap-0 py-0 shadow-2xs", id && "scroll-mt-6")}>
      <CardHeader className="flex flex-row items-center gap-3 px-5 py-4">
        <span className="bg-muted/70 text-muted-foreground grid size-9 shrink-0 place-items-center rounded-lg">
          <Icon className="size-[1.15rem]" strokeWidth={1.75} />
        </span>
        <div className="flex min-w-0 flex-col gap-0.5">
          <CardTitle className="text-sm">{title}</CardTitle>
          {subtitle && <p className="text-muted-foreground text-xs">{subtitle}</p>}
        </div>
        {aside && <div className="ml-auto shrink-0">{aside}</div>}
      </CardHeader>
      {children != null && (
        <CardContent className="border-border/60 flex flex-col gap-5 border-t px-5 py-5">
          {children}
        </CardContent>
      )}
    </Card>
  );
}
