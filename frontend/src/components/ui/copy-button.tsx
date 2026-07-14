import { CheckIcon, CopyIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** How long the button stays in its "copied" state before reverting. */
const COPIED_FEEDBACK_MS = 2000;

/** A button that puts `text` on the clipboard and confirms with a checkmark.
 *
 * Icon-only by default; `withLabel` spells the action out for the places where
 * copying is the primary act of the screen (the once-shown secret dialogs). */
function CopyButton({
  text,
  withLabel = false,
  className,
}: {
  text: string;
  withLabel?: boolean;
  className?: string;
}) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const label = copied ? t("common.copied") : t("common.copy");

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => {
      setCopied(false);
    }, COPIED_FEEDBACK_MS);
    return () => {
      clearTimeout(timer);
    };
  }, [copied]);

  return (
    <Button
      variant={withLabel ? "default" : "ghost"}
      size={withLabel ? "default" : "icon-sm"}
      className={cn(
        withLabel
          ? "shrink-0"
          : // The pseudo-element pads the pointer target out to the 44px comfortable-touch
            // size without growing the button's visual box or disturbing the layout.
            "text-muted-foreground hover:text-foreground relative shrink-0 after:absolute after:-inset-1.5 after:content-[''] [&_svg:not([class*='size-'])]:size-4",
        className,
      )}
      aria-label={withLabel ? undefined : label}
      onClick={() => {
        void navigator.clipboard.writeText(text).then(
          () => {
            setCopied(true);
          },
          () => undefined, // clipboard blocked (insecure context) — nothing to report
        );
      }}
    >
      {copied ? <CheckIcon /> : <CopyIcon />}
      {withLabel ? label : null}
    </Button>
  );
}

export { CopyButton };
