import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

/** Shared marker for a feature that has not shipped yet: a neutral pill with a
 * quiet dot. Grey by design — reads as "not here yet", never as an alert. */
export function InDevelopmentBadge({ className }: { className?: string }) {
  const { t } = useTranslation();
  return (
    <span
      className={cn(
        "border-border bg-background/60 text-muted-foreground inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium",
        className,
      )}
    >
      <span className="bg-muted-foreground/50 size-1.5 rounded-full" />
      {t("common.inDevelopment")}
    </span>
  );
}
