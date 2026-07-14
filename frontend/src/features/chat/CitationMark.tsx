import { useAuiState } from "@assistant-ui/react";
import type { ComponentProps } from "react";
import { useTranslation } from "react-i18next";
import type { ExtraProps } from "react-markdown";

import { postAccess } from "./api";
import type { MessageOverlay } from "./types";

/** One inline [n] citation. Styled to echo the source chips at the foot of the
 * bubble — the same numbered badge — so the eye ties a sentence to its source
 * below. And, like those chips, a click jumps straight to the source page
 * instead of opening a card. While the answer streams (citations not in yet) or
 * when the model invented a number, it degrades to a quiet, inert superscript. */
export function CitationMark({ node }: ComponentProps<"cite"> & ExtraProps) {
  const { t } = useTranslation();
  const marker = Number(node?.properties.marker);
  const custom = useAuiState((state) => state.message.metadata.custom);
  const overlay = (custom as { overlay?: MessageOverlay } | undefined)?.overlay;
  const citation = overlay?.citations.find((item) => item.marker === marker);

  if (!citation || Number.isNaN(marker)) {
    return (
      <sup className="text-muted-foreground px-0.5 text-[0.7em] font-medium">
        {Number.isNaN(marker) ? null : marker}
      </sup>
    );
  }

  // The numbered badge from MessageSources, shrunk to sit inline in prose. An
  // accent tint reads as a link and stays legible in dark theme; the transparent
  // `before` pad widens the click target well past the circle's 18px.
  const badge =
    "bg-primary/10 text-primary ring-primary/30 relative mx-0.5 inline-flex size-4.5 shrink-0 -translate-y-px items-center justify-center rounded-full align-middle font-mono text-[11px] leading-none ring-1 before:absolute before:-inset-1 before:content-['']";

  const title = citation.title ?? t("chat.sources.untitled");

  // No link → nothing to jump to; render an inert, hover-titled badge.
  if (!citation.url) {
    return (
      <span title={title} className={badge}>
        {marker}
      </span>
    );
  }

  // Opening a source is a demand signal — fire-and-forget (retrieval.html#access-signal).
  const signalClick = () => {
    if (overlay?.conversationId == null) return;
    void postAccess(overlay.conversationId, citation.entity_id).catch(() => {});
  };

  return (
    <a
      href={citation.url}
      target="_blank"
      rel="noopener noreferrer"
      title={title}
      onClick={signalClick}
      className={`${badge} hover:bg-primary hover:text-primary-foreground hover:ring-primary focus-visible:ring-ring no-underline transition-colors outline-none focus-visible:ring-2`}
    >
      {marker}
    </a>
  );
}
