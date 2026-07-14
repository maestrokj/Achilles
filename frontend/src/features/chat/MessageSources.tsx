import { useTranslation } from "react-i18next";

import { postAccess } from "./api";
import type { Citation } from "./types";

/** Source chips stitched to the foot of the answer bubble — one pill per [n]
 * marker in the text, so the number ties each source back to the sentence it
 * backs. The snippet rides along as a native tooltip; titles never render HTML. */
export function MessageSources({
  citations,
  conversationId,
}: {
  citations: Citation[];
  conversationId: number | null;
}) {
  const { t } = useTranslation();

  // Opening a source is the second demand signal, next to citation. Fire-and-
  // forget: a lost signal must never disrupt the click (retrieval.html#access-signal).
  const signalClick = (entityId: number) => {
    if (conversationId === null) return;
    void postAccess(conversationId, entityId).catch(() => {});
  };

  const chipClass =
    "inline-flex max-w-[15rem] items-center gap-1.5 rounded-full border border-border/70 bg-background/60 py-1 pr-2.5 pl-1 text-xs text-foreground";

  return (
    <div className="border-border/60 mt-3 flex flex-col gap-2 border-t pt-2.5">
      <p className="text-muted-foreground text-[11px] font-medium tracking-wide uppercase">
        {t("chat.sources.title")}
      </p>
      <div className="flex flex-wrap gap-1.5">
        {citations.map((citation) => {
          const title = citation.title ?? t("chat.sources.untitled");
          const marker = (
            <span className="bg-secondary text-secondary-foreground flex size-4.5 shrink-0 items-center justify-center rounded-full font-mono text-[10px] leading-none">
              {citation.marker}
            </span>
          );
          const label = <span className="truncate">{title}</span>;

          return citation.url ? (
            <a
              key={citation.marker}
              href={citation.url}
              target="_blank"
              rel="noopener noreferrer"
              title={citation.snippet ?? undefined}
              onClick={() => {
                signalClick(citation.entity_id);
              }}
              className={`${chipClass} hover:border-border hover:bg-accent transition-colors`}
            >
              {marker}
              {label}
            </a>
          ) : (
            <span key={citation.marker} title={citation.snippet ?? undefined} className={chipClass}>
              {marker}
              {label}
            </span>
          );
        })}
      </div>
    </div>
  );
}
