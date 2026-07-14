/** One derived phase for the embedding model, shared by the three Admin
 * screens that show it (AI models, Knowledge Store, Harvester):
 *
 *   none → loading (weights) → reembedding (N%) → ready, plus error/offline.
 *
 * Weights loading and the re-embed run are separate facts from separate
 * owners. The curation journal side arrives as push nudges over the events
 * stream (features/live); the runtime side lives in the embeddings
 * microservice, which nothing on our side can publish for — so this hook
 * keeps the platform's ONE residual poll, gated to the volatile window
 * (weights loading / a re-embed switching them). */

import { useEffect, useState } from "react";

import { useQuery } from "@tanstack/react-query";

import { LIVE_STALE_TIME } from "@/api/freshness";
import { getCurationStatus, knowledgeKeys } from "@/features/admin/knowledge/api";

import { aiKeys, getEmbedderStatus } from "./api";

const ACTIVE_POLL_MS = 1500;
// After a successful model-change re-embed ends, keep the "reembedding" phase
// alive at 100% for this beat so the bar visibly fills before the card flips to
// "done". A small-corpus run finishes between two polls and would otherwise snap
// 0% → gone with nothing for the eye to follow. The bar's own CSS transition
// carries the fill; this only holds the frame open long enough to see it.
const REEMBED_FINISH_HOLD_MS = 900;

export type EmbedderPhase = "none" | "loading" | "reembedding" | "error" | "offline" | "ready";

interface ReembedInfo {
  done: number;
  total: number;
  /** Display names of the switch endpoints; null — the model row is gone. */
  from_model?: string | null;
  to_model?: string | null;
}

export interface EmbedderPhaseInfo {
  phase: EmbedderPhase;
  /** Assigned model's display name; null while nothing is assigned. */
  displayName: string | null;
  /** Re-embed progress, 0–100; null outside the reembedding phase. */
  percent: number | null;
  /** Re-embed endpoints + counts; retained through the finish hold. */
  reembed: ReembedInfo | null;
  /** The runtime's load error; null outside the error phase. */
  error: string | null;
  isPending: boolean;
}

export function useEmbedderPhase(): EmbedderPhaseInfo {
  const curation = useQuery({
    queryKey: knowledgeKeys.curation,
    queryFn: getCurationStatus,
    staleTime: LIVE_STALE_TIME,
  });
  const reembedActive = curation.data?.active?.trigger === "model_change";
  const lastSucceeded = curation.data?.last?.state === "succeeded";

  // Finish flourish: linger on "reembedding" at 100% for a beat after a run we
  // actually saw active ends successfully. A run that slipped entirely between
  // polls (never observed active) has nothing to fill from — no flourish; a
  // cancelled/failed run gets an honest snap to its outcome, not a false 100%.
  //
  // The active→idle edge and the last live snapshot are captured during render
  // (React's "adjust state on change" pattern); the effect only arms the timer
  // that ends the hold, so no state is set synchronously inside it.
  const [wasActive, setWasActive] = useState(reembedActive);
  const [holding, setHolding] = useState(false);
  const [snapshot, setSnapshot] = useState<ReembedInfo | null>(null);

  if (reembedActive !== wasActive) {
    setWasActive(reembedActive);
    // A run just ended (was active, now not) and succeeded → open the hold.
    setHolding(!reembedActive && lastSucceeded);
  }
  if (reembedActive && curation.data?.reembed && curation.data.reembed !== snapshot) {
    setSnapshot(curation.data.reembed);
  }

  useEffect(() => {
    if (!holding) return;
    const id = window.setTimeout(() => {
      setHolding(false);
    }, REEMBED_FINISH_HOLD_MS);
    return () => {
      window.clearTimeout(id);
    };
  }, [holding]);

  const embedder = useQuery({
    queryKey: aiKeys.embedder,
    queryFn: getEmbedderStatus,
    staleTime: LIVE_STALE_TIME,
    // Poll while the runtime is volatile. Weights loading is the obvious case,
    // but a model-change re-embed is the subtle one: while it runs, the runtime
    // swaps weights and hammers the CPU, so a /admin/status probe can time out to
    // a *transient* unreachable/not_loaded. If we stopped polling on that sample
    // (as "loading-only" did), the query froze on a false-offline that stayed
    // hidden behind the reembedding phase and then surfaced — stuck — the moment
    // the run ended, until a manual refresh. Keeping the poll alive across the
    // switch window (run active + the finish hold, so a transient sampled on the
    // very last tick still gets one corrective poll) until the runtime settles
    // to ready/external lets it self-heal.
    refetchInterval: (query) => {
      const state = query.state.data?.runtime?.state;
      if (state === "loading") return ACTIVE_POLL_MS;
      if ((reembedActive || holding) && state !== "ready" && state !== "external") {
        return ACTIVE_POLL_MS;
      }
      return false;
    },
    // The admin who kicked a switch often waits in another window; the poll
    // only exists while a load is active, so ticking without focus is cheap.
    refetchIntervalInBackground: true,
  });

  const assigned = embedder.data?.assigned ?? null;
  const runtime = embedder.data?.runtime ?? null;
  const reembedShowing = reembedActive || holding;

  let phase: EmbedderPhase = "ready";
  if (assigned === null) {
    phase = "none";
  } else if (runtime?.state === "loading") {
    // Weights first: a model_change run may already be journalled, but until
    // the runtime is ready the honest phase is "loading", not a frozen 0%.
    phase = "loading";
  } else if (reembedShowing) {
    phase = "reembedding";
  } else if (runtime?.state === "error") {
    phase = "error";
  } else if (runtime?.state === "unreachable" || runtime?.state === "not_loaded") {
    phase = "offline";
  }

  // During the hold the run has finished, so it is fully done — present the
  // counts as total/total to match the filled bar.
  const holdReembed: ReembedInfo | null = snapshot ? { ...snapshot, done: snapshot.total } : null;
  const reembed = reembedActive ? (curation.data?.reembed ?? null) : holding ? holdReembed : null;

  let percent: number | null = null;
  if (phase === "reembedding") {
    percent =
      holding && !reembedActive
        ? 100
        : reembed && reembed.total > 0
          ? Math.round((reembed.done / reembed.total) * 100)
          : 0;
  }

  return {
    phase,
    displayName: assigned?.display_name ?? null,
    percent,
    reembed,
    error: phase === "error" ? (runtime?.error ?? null) : null,
    isPending: embedder.isPending || curation.isPending,
  };
}
