/** Pill-link to the AI models screen carrying the embedder phase — the one
 * element Knowledge Store and Harvester share to point at the assignment.
 * Reserves its height while the phase loads (no layout shift). */

import { ArrowUpRightIcon, SparklesIcon, TriangleAlertIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";

import { useEmbedderPhase, type EmbedderPhase } from "./useEmbedderPhase";

const ASSIGNMENTS_LINK = "/admin/ai-models#assignments";

const PILL_BASE =
  "inline-flex w-fit items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors";

const PILL_TONE: Record<EmbedderPhase, string> = {
  ready: "border-success/40 bg-success/10 text-success hover:bg-success/20 group",
  none: "border-warning/40 bg-warning/10 text-warning hover:bg-warning/20",
  loading: "border-warning/40 bg-warning/10 text-warning hover:bg-warning/20",
  reembedding: "border-warning/40 bg-warning/10 text-warning hover:bg-warning/20",
  error: "border-destructive/40 bg-destructive/10 text-destructive hover:bg-destructive/20",
  offline: "border-border bg-muted/50 text-muted-foreground hover:bg-muted",
};

/** `live` off — a fuller run panel sits right below (Knowledge Store): the pill
 * drops to a calm identity chip so the panel owns the spinner and percent, and
 * the screen shows one moving indicator, not three. On its own (Harvester) the
 * pill stays live. */
export function EmbedderPill({ live = true }: { live?: boolean }) {
  const { t } = useTranslation();
  const { phase, displayName, percent, error, isPending } = useEmbedderPhase();

  if (isPending) {
    return <Skeleton className="h-[26px] w-40 rounded-full" />;
  }

  const busy = phase === "loading" || phase === "reembedding";
  const calm = !live && busy;
  const label = calm
    ? (displayName ?? "")
    : {
        none: t("admin.aiModels.embedderNotAssigned"),
        loading: t("admin.aiModels.weightsLoading"),
        reembedding: t("admin.aiModels.reembedRunning", { percent: percent ?? 0 }),
        error: t("admin.aiModels.runtimeError"),
        offline: t("admin.aiModels.runtimeOffline"),
        ready: displayName ?? "",
      }[phase];

  return (
    <Link
      to={ASSIGNMENTS_LINK}
      title={phase === "error" ? (error ?? undefined) : undefined}
      className={cn(PILL_BASE, PILL_TONE[phase])}
    >
      {busy && !calm ? (
        <Spinner className="border-warning/40 border-t-warning size-3.5" />
      ) : phase === "ready" || calm ? (
        <SparklesIcon aria-hidden="true" className="size-3.5" />
      ) : (
        <TriangleAlertIcon aria-hidden="true" className="size-3.5" />
      )}
      {label}
      <ArrowUpRightIcon aria-hidden="true" className="size-3 opacity-70 transition-opacity" />
    </Link>
  );
}
