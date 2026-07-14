import { TriangleAlertIcon, type LucideIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type EmptyStateProps = {
  /** `empty` — a live section with no rows yet: icon in a disc, a title and a
   *  hint, plus an optional primary call to action (the product's zero state).
   *  Pass `filtered` to collapse it to the thin "no matches" line + reset when a
   *  search or facet matched nothing.
   *  `error` — the request failed. Compact form (description only) is the block
   *  weight: one line + a retry, sits inside a card among others. Pass `icon` +
   *  `title` for the screen weight: a disc, a heading and a muted hint — used
   *  when a whole route's single resource failed. The host owns the frame (table,
   *  card, page) and any full-height centering; this renders the placeholder. */
  variant?: "empty" | "error";
  /** When an `empty` list is the result of an active search/facet, collapse to
   *  the thin "no matches" form instead of the section's zero state. */
  filtered?: boolean;
  icon?: LucideIcon;
  title?: string;
  description?: string;
  action?: { label: string; icon?: LucideIcon; onClick: () => void };
  onReset?: () => void;
  onRetry?: () => void;
  className?: string;
};

export function EmptyState({
  variant = "empty",
  filtered,
  icon: Icon,
  title,
  description,
  action,
  onReset,
  onRetry,
  className,
}: EmptyStateProps) {
  const { t } = useTranslation();

  if (variant === "error") {
    const ErrorIcon = Icon ?? TriangleAlertIcon;
    const retry = onRetry && (
      <Button variant="outline" size="sm" className={cn(title && "mt-3")} onClick={onRetry}>
        {t("common.list.retry")}
      </Button>
    );
    // Screen weight: a disc, a heading and a muted hint. Block weight: a small
    // icon over one destructive line — enough presence without a poster.
    return title ? (
      <div className={cn("flex flex-col items-center gap-2 py-10 text-center", className)}>
        <div className="bg-destructive/10 mb-1 flex size-12 items-center justify-center rounded-full">
          <ErrorIcon className="text-destructive size-6" />
        </div>
        <p className="font-medium">{title}</p>
        <p className="text-muted-foreground max-w-md text-sm">
          {description ?? t("common.list.error")}
        </p>
        {retry}
      </div>
    ) : (
      <div className={cn("flex flex-col items-center gap-2 py-10 text-center", className)}>
        <ErrorIcon className="text-destructive size-5" />
        <p className="text-destructive text-sm">{description ?? t("common.list.error")}</p>
        {retry}
      </div>
    );
  }

  if (filtered)
    return (
      <div className={cn("flex flex-col items-center gap-2 py-8 text-center", className)}>
        <p className="text-muted-foreground text-sm">{t("common.list.noResults")}</p>
        {onReset && (
          <Button variant="ghost" size="sm" onClick={onReset}>
            {t("common.list.reset")}
          </Button>
        )}
      </div>
    );

  return (
    <div className={cn("flex flex-col items-center gap-2 py-12 text-center", className)}>
      {Icon && (
        <div className="bg-secondary mb-1 flex size-12 items-center justify-center rounded-full">
          <Icon className="text-muted-foreground size-6" />
        </div>
      )}
      {title && <p className="font-medium">{title}</p>}
      {description && <p className="text-muted-foreground max-w-md text-sm">{description}</p>}
      {action && (
        <Button className="mt-3" size="sm" onClick={action.onClick}>
          {action.icon && <action.icon data-icon="inline-start" />}
          {action.label}
        </Button>
      )}
    </div>
  );
}
