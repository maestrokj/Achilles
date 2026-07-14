import { useInfiniteQuery } from "@tanstack/react-query";
import { ChevronRightIcon, ClockIcon, CoinsIcon, HistoryIcon } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { EmptyState } from "@/components/list-controls/EmptyState";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import { formatDateTime, formatDuration, formatTokens } from "@/lib/format";
import { cn } from "@/lib/utils";

import type { AgentRun, Page, RunState } from "./types";

/** A run the worker is still driving — its bead renders live (pulse). */
const ACTIVE_STATES: ReadonlySet<RunState> = new Set(["queued", "running"]);

/** Timeline node styling per run state — semantic tokens only.
 *  `dot` paints the bead on the rail; `text` tints the state label to match. */
const STATE_NODE: Record<RunState, { dot: string; text: string }> = {
  queued: { dot: "bg-muted-foreground/40", text: "text-muted-foreground" },
  running: { dot: "bg-info", text: "text-info" },
  succeeded: { dot: "bg-success", text: "text-success" },
  failed: { dot: "bg-destructive", text: "text-destructive" },
  skipped: { dot: "bg-warning", text: "text-warning" },
};

interface RunJournalProps {
  queryKey: readonly unknown[];
  fetchPage: (cursor?: string | null) => Promise<Page<AgentRun>>;
}

/** The shared run journal: owner editor and the admin profile render the same
 *  timeline — a rail of status beads, one per run, newest first. */
export function RunJournal({ queryKey, fetchPage }: RunJournalProps) {
  const { t, i18n } = useTranslation();
  const [openId, setOpenId] = useState<number | null>(null);

  const query = useInfiniteQuery({
    queryKey,
    queryFn: ({ pageParam }) => fetchPage(pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
  });

  if (query.isPending) {
    return <Spinner className="mx-auto my-6" />;
  }
  if (query.isError) {
    return (
      <EmptyState
        variant="error"
        description={t("agents.errors.loadFailed")}
        onRetry={() => {
          void query.refetch();
        }}
      />
    );
  }

  const runs = query.data.pages.flatMap((page) => page.items);
  if (runs.length === 0) {
    return <EmptyState icon={HistoryIcon} description={t("agents.journal.empty")} />;
  }

  return (
    <div className="flex flex-col">
      <Card className="shadow-2xs">
        <div className="flex flex-col px-4">
          {runs.map((run, index) => {
            const open = openId === run.id;
            const node = STATE_NODE[run.state];
            const active = ACTIVE_STATES.has(run.state);
            return (
              <div key={run.id} className="flex gap-3.5">
                {/* Rail: a bead threaded on a hairline spine. */}
                <div className="relative w-3" aria-hidden>
                  {index > 0 && (
                    <span className="bg-border absolute top-0 left-1/2 h-[22px] w-px -translate-x-1/2" />
                  )}
                  {index < runs.length - 1 && (
                    <span className="bg-border absolute top-[22px] bottom-0 left-1/2 w-px -translate-x-1/2" />
                  )}
                  <span className="absolute top-[22px] left-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2">
                    {active && (
                      <span
                        className={cn(
                          "absolute inset-0 animate-ping rounded-full opacity-60",
                          node.dot,
                        )}
                      />
                    )}
                    <span
                      className={cn(
                        "ring-card relative block size-2.5 rounded-full ring-4",
                        node.dot,
                      )}
                    />
                  </span>
                </div>

                {/* Content column. */}
                <div className="min-w-0 flex-1 pt-3 pb-4">
                  <button
                    type="button"
                    className="group flex w-full flex-col gap-1 text-left"
                    onClick={() => {
                      setOpenId(open ? null : run.id);
                    }}
                  >
                    <div className="flex items-center gap-2">
                      <span className={cn("text-sm font-medium", node.text)}>
                        {t(`agents.runState.${run.state}`)}
                      </span>
                      {/* "error" just restates the "failed" label — the real
                          message lives in run.error, shown on expand. */}
                      {run.reason && run.reason !== "error" && (
                        <span className="text-muted-foreground truncate text-xs">
                          · {t(`agents.runReason.${run.reason}`)}
                        </span>
                      )}
                      <ChevronRightIcon
                        className={cn(
                          "text-muted-foreground/60 group-hover:text-muted-foreground ml-auto size-4 shrink-0 transition-transform",
                          open && "rotate-90",
                        )}
                      />
                    </div>
                    <div className="text-muted-foreground flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs tabular-nums">
                      <span>
                        {formatDateTime(run.finished_at ?? run.created_at, i18n.language)}
                      </span>
                      <Sep />
                      <span>{t(`agents.trigger.${run.trigger}`)}</span>
                      {run.duration_seconds !== null && (
                        <>
                          <Sep />
                          <span className="inline-flex items-center gap-1">
                            <ClockIcon className="size-3 opacity-70" />
                            {formatDuration(run.duration_seconds, i18n.language)}
                          </span>
                        </>
                      )}
                      <Sep />
                      <span className="inline-flex items-center gap-1">
                        <CoinsIcon className="size-3 opacity-70" />
                        {formatTokens(run.tokens_used, i18n.language)}
                      </span>
                    </div>
                  </button>

                  {open && (
                    <div className="animate-in fade-in slide-in-from-top-1 mt-3 flex flex-col gap-2 duration-200">
                      {run.error && (
                        <p className="border-destructive/20 bg-destructive/5 text-destructive rounded-md border px-3 py-2 text-xs">
                          {run.error}
                        </p>
                      )}
                      {run.output ? (
                        <p className="border-l-primary/40 bg-muted/50 rounded-md rounded-l-none border-l-2 px-3.5 py-2.5 text-sm whitespace-pre-wrap">
                          {run.output}
                        </p>
                      ) : (
                        !run.error && (
                          <p className="text-muted-foreground text-xs italic">
                            {t("agents.journal.noOutput")}
                          </p>
                        )
                      )}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </Card>
      {query.hasNextPage && (
        <Button
          variant="ghost"
          size="sm"
          className="mt-2 self-center"
          disabled={query.isFetchingNextPage}
          onClick={() => {
            void query.fetchNextPage();
          }}
        >
          {t("agents.journal.loadMore")}
        </Button>
      )}
    </div>
  );
}

/** Muted middot separating the run's meta facts. */
function Sep() {
  return (
    <span className="text-muted-foreground/40" aria-hidden>
      ·
    </span>
  );
}
