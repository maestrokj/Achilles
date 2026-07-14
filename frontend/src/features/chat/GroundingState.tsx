import { EyeOffIcon, SearchXIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { Grounding } from "./types";

/** Honesty banner under a grounded answer: "empty" and "acl_hidden" get a
 * plaque; "found" speaks through the source cards; conversational stays silent. */
export function GroundingState({ grounding }: { grounding: Grounding }) {
  const { t } = useTranslation();

  if (grounding.mode !== "grounded") return null;

  if (grounding.outcome === "empty") {
    return (
      <div className="border-border bg-muted text-muted-foreground flex items-start gap-2 rounded-lg border px-3 py-2 text-xs">
        <SearchXIcon className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
        {t("chat.grounding.empty")}
      </div>
    );
  }

  if (grounding.outcome === "acl_hidden") {
    const { hidden_source_type: source, hidden_author_email: author } = grounding;
    return (
      <div className="border-border bg-muted text-muted-foreground flex items-start gap-2 rounded-lg border px-3 py-2 text-xs">
        <EyeOffIcon className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
        {source && author
          ? t("chat.grounding.aclHidden", { source, author })
          : t("chat.grounding.aclHiddenNoDetails")}
      </div>
    );
  }

  return null;
}
