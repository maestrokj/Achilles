import { ThumbsDownIcon, ThumbsUpIcon } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { setFeedback } from "./api";
import type { FeedbackValue } from "./types";

/** 👍/👎 on an assistant message — optimistic PATCH, a second click clears the vote. */
export function Feedback({ messageId, initial }: { messageId: number; initial: FeedbackValue }) {
  const { t } = useTranslation();
  const [value, setValue] = useState<FeedbackValue>(initial);

  const toggle = (vote: 1 | -1) => {
    const previous = value;
    const next: FeedbackValue = value === vote ? null : vote;
    setValue(next);
    setFeedback(messageId, next).catch(() => {
      setValue(previous); // roll the optimistic flip back
    });
  };

  return (
    <div className="ml-1 flex items-center gap-1">
      <Button
        variant="ghost"
        size="icon-xs"
        aria-label={t("chat.feedback.up")}
        aria-pressed={value === 1}
        className={cn("text-muted-foreground", value === 1 && "text-primary")}
        onClick={() => {
          toggle(1);
        }}
      >
        <ThumbsUpIcon />
      </Button>
      <Button
        variant="ghost"
        size="icon-xs"
        aria-label={t("chat.feedback.down")}
        aria-pressed={value === -1}
        className={cn("text-muted-foreground", value === -1 && "text-primary")}
        onClick={() => {
          toggle(-1);
        }}
      >
        <ThumbsDownIcon />
      </Button>
    </div>
  );
}
