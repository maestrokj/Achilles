import { InfoIcon } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";

/** Muted icon that reveals an explanatory note on hover — sits beside a label
 * to carry a caveat or hint without spending a permanent line of the layout. */
export function InfoHint({
  text,
  label,
  className,
}: {
  text: ReactNode;
  /** Accessible name for the trigger; defaults to the generic "more info". */
  label?: string;
  className?: string;
}) {
  const { t } = useTranslation();
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger
          render={
            <button
              type="button"
              aria-label={label ?? t("common.moreInfo")}
              className={
                className ??
                "text-muted-foreground/70 hover:text-foreground inline-flex align-middle transition-colors"
              }
            />
          }
        >
          <InfoIcon className="size-3.5" aria-hidden="true" />
        </TooltipTrigger>
        <TooltipContent className="max-w-xs leading-relaxed">{text}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
