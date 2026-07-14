import { TriangleAlertIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { EmptyState } from "@/components/list-controls/EmptyState";
import { cn } from "@/lib/utils";

/** Screen-weight error placeholder for a route whose single resource failed to
 * load: the shared error EmptyState centered in a page frame. Mirrors
 * PageSkeleton — same default max-w-3xl frame, pass `className` to override
 * width/height. `description` defaults to the generic "couldn't load" hint. */
export function PageError({
  onRetry,
  description,
  className,
}: {
  onRetry: () => void;
  description?: string;
  className?: string;
}) {
  const { t } = useTranslation();
  return (
    <div
      className={cn(
        "mx-auto flex min-h-[60vh] w-full max-w-3xl items-center justify-center",
        className,
      )}
    >
      <EmptyState
        variant="error"
        icon={TriangleAlertIcon}
        title={t("common.list.errorTitle")}
        description={description ?? t("common.list.errorHint")}
        onRetry={onRetry}
      />
    </div>
  );
}
