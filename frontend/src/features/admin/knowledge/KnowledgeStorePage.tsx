import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArchiveIcon,
  CheckIcon,
  CircleSlashIcon,
  ClockIcon,
  RefreshCwIcon,
  TriangleAlertIcon,
  type LucideIcon,
} from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { toastApiError } from "@/api/errors";
import { LIVE_STALE_TIME } from "@/api/freshness";
import { toast } from "@/lib/toast";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { ComingSoonCard } from "@/components/ComingSoonCard";
import { StatusLine, type StatusTone } from "@/components/StatusLine";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { EmptyState } from "@/components/list-controls/EmptyState";
import { Skeleton } from "@/components/ui/skeleton";
import { EmbedderPill } from "@/features/admin/ai/EmbedderPill";
import { useEmbedderPhase } from "@/features/admin/ai/useEmbedderPhase";
import { harvesterKeys, listSources } from "@/features/admin/harvester/api";
import {
  getPlatformSettings,
  patchPlatformSettings,
  platformKeys,
} from "@/features/admin/platform/api";
import { isOwner } from "@/features/auth/roles";
import { useSession } from "@/features/auth/session-context";
import { runStateBadgeVariant, runStateLabel, runStateTone } from "@/lib/badges";
import {
  WEEKDAYS,
  formatBytes,
  formatDurationBetween,
  formatNumber,
  formatWhen,
  weekdayLong,
} from "@/lib/format";
import { useHashTarget } from "@/lib/useHashTarget";

import {
  cancelCuration,
  getBackupSettings,
  getCurationStatus,
  getMetrics,
  knowledgeKeys,
  listBackups,
  patchBackupSettings,
  startCuration,
  startRestore,
} from "./api";
import type { BackupSettings, BackupSnapshot, CurationStatus } from "./types";

/** Admin · Knowledge Store: storage tiles, curation panel, schedule, backups.
 * Wireframe: admin-panel/_wireframes/knowledge-store.html. */
export function KnowledgeStorePage() {
  const { t } = useTranslation();
  useHashTarget();
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
      <h1 className="text-2xl font-semibold tracking-tight">{t("admin.nav.knowledgeStore")}</h1>
      <MetricsCard />
      <CurationCard />
      <BackupCard />
      <ComingSoonCard
        icon={ArchiveIcon}
        title={t("admin.knowledge.retention")}
        note={t("admin.knowledge.retentionSoon")}
      />
    </div>
  );
}

function MetricsCard() {
  const { t, i18n } = useTranslation();
  const [sourceId, setSourceId] = useState<number | null>(null);
  const sources = useQuery({ queryKey: harvesterKeys.sources, queryFn: listSources });
  const metrics = useQuery({
    queryKey: knowledgeKeys.metrics(sourceId),
    queryFn: () => getMetrics(sourceId),
    // Keep the current tiles on screen while a new source loads — no skeleton flash.
    placeholderData: keepPreviousData,
    staleTime: LIVE_STALE_TIME,
  });
  const tile = (label: string, value: string, opts?: { muted?: boolean; hint?: string }) => (
    <div className="min-w-28 flex-1">
      <div
        className={`text-2xl font-semibold tabular-nums ${opts?.muted ? "text-muted-foreground" : ""}`}
      >
        {value}
      </div>
      <div className="text-muted-foreground text-xs" title={opts?.hint}>
        {label}
      </div>
    </div>
  );

  return (
    <Card className="shadow-2xs">
      <CardHeader className="border-b">
        <CardTitle className="text-sm font-semibold">{t("admin.knowledge.storage")}</CardTitle>
        <CardAction>
          <Select
            items={[
              { value: "all", label: t("admin.knowledge.allSources") },
              ...(sources.data ?? []).map((source) => ({
                value: String(source.id),
                label: source.name,
              })),
            ]}
            value={sourceId === null ? "all" : String(sourceId)}
            onValueChange={(value) => {
              if (value) setSourceId(value === "all" ? null : Number(value));
            }}
          >
            <SelectTrigger size="sm" className="w-48">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t("admin.knowledge.allSources")}</SelectItem>
              {(sources.data ?? []).map((source) => (
                <SelectItem key={source.id} value={String(source.id)}>
                  {source.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </CardAction>
      </CardHeader>
      <CardContent>
        {metrics.isPending ? (
          <Skeleton className="h-16 w-full" />
        ) : metrics.isError ? (
          <EmptyState
            variant="error"
            description={t("common.list.errorTitle")}
            onRetry={() => {
              void metrics.refetch();
            }}
          />
        ) : (
          <div className="flex flex-wrap gap-x-4 gap-y-5">
            {tile(
              t("admin.knowledge.entities"),
              formatNumber(metrics.data.entities, i18n.language),
            )}
            {tile(t("admin.knowledge.chunks"), formatNumber(metrics.data.chunks, i18n.language))}
            {tile(t("admin.knowledge.edges"), formatNumber(metrics.data.edges, i18n.language))}
            {tile(
              t("admin.knowledge.pendingRefs"),
              formatNumber(metrics.data.pending_refs, i18n.language),
              { muted: true, hint: t("admin.knowledge.pendingRefsHint") },
            )}
            {tile(t("admin.knowledge.vectorVolume"), formatBytes(metrics.data.vector_bytes))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/** Tone + icon + status headline for the last (idle) curation run. */
function idleVisual(state: string): { tone: StatusTone; icon: LucideIcon; key: string } {
  const tone = runStateTone(state);
  if (state === "failed")
    return { tone, icon: TriangleAlertIcon, key: "admin.knowledge.idleFailed" };
  if (state === "cancelled")
    return { tone, icon: CircleSlashIcon, key: "admin.knowledge.idleCancelled" };
  return { tone, icon: CheckIcon, key: "admin.knowledge.idleOk" };
}

function CurationCard() {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const status = useQuery({
    queryKey: knowledgeKeys.curation,
    queryFn: getCurationStatus,
    staleTime: LIVE_STALE_TIME,
  });
  // Weights-loading vs re-indexing: the phase hook also polls while either runs
  // and carries the finish flourish that fills the bar before the card settles.
  const { phase, percent, reembed: reembedInfo } = useEmbedderPhase();
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [confirmRun, setConfirmRun] = useState(false);

  const refresh = () => queryClient.invalidateQueries({ queryKey: knowledgeKeys.curation });
  const run = useMutation({
    mutationFn: startCuration,
    onSuccess: () => {
      setConfirmRun(false);
      void refresh();
    },
    onError: (error) => void toastApiError(error, t("admin.knowledge.startFailed")),
  });
  const cancel = useMutation({
    mutationFn: cancelCuration,
    onSuccess: () => {
      setConfirmCancel(false);
      void refresh();
    },
    onError: (error) => void toastApiError(error, t("admin.platform.saveFailed")),
  });

  if (status.isPending) return <Skeleton className="h-40 w-full" />;
  if (status.isError)
    return (
      <Card className="shadow-2xs">
        <CardHeader className="border-b">
          <CardTitle className="text-sm font-semibold">{t("admin.knowledge.curation")}</CardTitle>
        </CardHeader>
        <CardContent>
          <EmptyState
            variant="error"
            description={t("common.list.errorTitle")}
            onRetry={() => {
              void status.refetch();
            }}
          />
        </CardContent>
      </Card>
    );
  const { active } = status.data;
  // The finish flourish keeps the reembed frame open for a beat after `active`
  // clears, so the running panel follows the phase, not only the live run row.
  const reembedShowing = phase === "reembedding";
  const showRunning = active !== null || reembedShowing;
  const isReembed = active?.trigger === "model_change" || reembedShowing;
  const trigger = active?.trigger ?? "model_change";

  return (
    <Card className="shadow-2xs">
      <CardHeader className="border-b">
        <CardTitle className="text-sm font-semibold">{t("admin.knowledge.curation")}</CardTitle>
        <CardAction>
          <Button
            size="sm"
            disabled={active !== null || run.isPending}
            onClick={() => {
              setConfirmRun(true);
            }}
          >
            <RefreshCwIcon className="size-4" />
            {t("admin.knowledge.runNow")}
          </Button>
          <AlertDialog open={confirmRun} onOpenChange={setConfirmRun}>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>{t("admin.knowledge.runNow")}</AlertDialogTitle>
                <AlertDialogDescription>{t("admin.knowledge.runNowHint")}</AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>{t("admin.platform.cancel")}</AlertDialogCancel>
                <AlertDialogAction
                  onClick={() => {
                    run.mutate();
                  }}
                >
                  {t("admin.platform.confirm")}
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </CardAction>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        <div className="flex flex-col gap-1.5">
          <Label>{t("admin.knowledge.embedder")}</Label>
          <EmbedderPill live={false} />
        </div>
        {showRunning ? (
          <StatusLine
            tone="primary"
            icon={RefreshCwIcon}
            spinning
            primary={t("admin.knowledge.running")}
            meta={t(`admin.knowledge.triggers.${trigger}`)}
          >
            <div className="flex flex-col gap-2 pl-11">
              {active?.destructive_open && (
                <p className="text-warning text-xs">{t("admin.knowledge.destructiveOpen")}</p>
              )}
              {isReembed && phase === "loading" && (
                // Weights are still arriving — an honest line instead of a
                // progress bar frozen at 0%. The StatusLine header carries the
                // motion, so no spinner of its own.
                <span className="text-muted-foreground text-xs">
                  {t("admin.aiModels.weightsLoading")}
                </span>
              )}
              {isReembed && phase !== "loading" && reembedInfo && (
                <div className="flex flex-col gap-1">
                  <Progress
                    value={percent ?? 0}
                    // A slow ease-out lets the bar glide between polls and,
                    // crucially, fill to 100% on the finish flourish.
                    className="max-w-md [&_[data-slot=progress-indicator]]:duration-700 [&_[data-slot=progress-indicator]]:ease-out"
                  />
                  <span className="text-muted-foreground text-xs tabular-nums">
                    {t("admin.knowledge.reembedProgress", {
                      done: formatNumber(reembedInfo.done, i18n.language),
                      total: formatNumber(reembedInfo.total, i18n.language),
                    })}
                  </span>
                  {(reembedInfo.from_model ?? reembedInfo.to_model) != null && (
                    <span className="text-muted-foreground text-xs">
                      {t("admin.knowledge.reembedModels", {
                        from: reembedInfo.from_model ?? t("admin.knowledge.removedModel"),
                        to: reembedInfo.to_model ?? t("admin.knowledge.removedModel"),
                      })}
                    </span>
                  )}
                </div>
              )}
              <p className="text-muted-foreground text-xs">{t("admin.knowledge.searchStaysUp")}</p>
              {active && (
                <Button
                  variant="outline"
                  size="sm"
                  className="mt-1 self-start"
                  onClick={() => {
                    setConfirmCancel(true);
                  }}
                >
                  {t("admin.knowledge.cancelRun")}
                </Button>
              )}
            </div>
            {active && (
              <AlertDialog open={confirmCancel} onOpenChange={setConfirmCancel}>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>{t("admin.knowledge.cancelRun")}</AlertDialogTitle>
                    <AlertDialogDescription>
                      {t("admin.knowledge.cancelHint")}
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>{t("admin.platform.cancel")}</AlertDialogCancel>
                    <AlertDialogAction
                      onClick={() => {
                        cancel.mutate(active.id);
                      }}
                    >
                      {t("admin.platform.confirm")}
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            )}
          </StatusLine>
        ) : (
          <IdleSummary status={status.data} locale={i18n.language} />
        )}
        <ScheduleSection nextScheduled={status.data.next_scheduled} />
      </CardContent>
    </Card>
  );
}

/** curation_runs.steps keys the page can label (knowledge_store/jobs.py). */
const STEP_KEYS = [
  "refs_materialized",
  "duplicates_merged",
  "entities_rescored",
  "reembedded",
] as const;

/** "84 duplicates merged · 1,200 links added" — the idle recap in plain words;
 * a step may carry "skipped" / "error" instead of a number. */
function stepsSummary(
  steps: Record<string, unknown>,
  t: ReturnType<typeof useTranslation>["t"],
  locale: string,
): string | null {
  // A plain-string view of t: resolving these keys through the full i18next
  // KeyPath type blows the instantiation depth (TS2589) on this deep subtree.
  const tr = t as unknown as (key: string) => string;
  const parts: string[] = [];
  for (const key of STEP_KEYS) {
    const value = steps[key];
    if (value === undefined) continue;
    const label = tr(`admin.knowledge.stepNames.${key}`);
    if (typeof value === "number") parts.push(`${formatNumber(value, locale)} ${label}`);
    else if (value === "skipped") parts.push(`${label} — ${tr("admin.knowledge.stepSkipped")}`);
    else if (value === "error") parts.push(`${label} — ${tr("admin.knowledge.stepError")}`);
  }
  return parts.length > 0 ? parts.join(" · ") : null;
}

function IdleSummary({ status, locale }: { status: CurationStatus; locale: string }) {
  const { t } = useTranslation();
  const last = status.last;
  if (!last) {
    return <StatusLine tone="muted" icon={ClockIcon} primary={t("admin.knowledge.neverRan")} />;
  }
  const visual = idleVisual(last.state);
  const finished = formatWhen(last.finished_at, locale);
  const duration = formatDurationBetween(last.started_at, last.finished_at, locale);
  const meta = [finished, duration].filter(Boolean).join(" · ");
  const summary = last.steps ? stepsSummary(last.steps, t, locale) : null;
  return (
    <StatusLine
      tone={visual.tone}
      icon={visual.icon}
      primary={t(visual.key as "admin.knowledge.idleOk")}
      meta={meta || undefined}
    >
      {last.error ? (
        <p className="text-destructive pl-11 text-xs">{last.error}</p>
      ) : (
        summary && <p className="text-muted-foreground pl-11 text-xs tabular-nums">{summary}</p>
      )}
    </StatusLine>
  );
}

/** The curation window lives in platform_settings — writes go through PATCH /admin/settings. */
function ScheduleSection({ nextScheduled }: { nextScheduled: string | null }) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const settings = useQuery({ queryKey: platformKeys.settings, queryFn: getPlatformSettings });
  const [draft, setDraft] = useState<{
    frequency: "daily" | "weekly";
    weekday: number;
    time: string;
  } | null>(null);
  const session = useSession();
  const readOnly = !isOwner(session.user?.role);

  const save = useMutation({
    mutationFn: patchPlatformSettings,
    onSuccess: (fresh) => {
      queryClient.setQueryData(platformKeys.settings, fresh);
      void queryClient.invalidateQueries({ queryKey: knowledgeKeys.curation });
      setDraft(null);
      toast.success(t("admin.platform.saved"));
    },
    onError: (error) => void toastApiError(error, t("admin.platform.saveFailed")),
  });

  // Platform settings arrive after the curation card itself, so the schedule block
  // holds its height rather than materializing under the already-painted card.
  if (settings.isPending)
    return (
      <div className="flex flex-col gap-3 border-t pt-4">
        <div className="flex flex-col gap-0.5">
          <h3 className="text-sm font-medium">{t("admin.knowledge.schedule")}</h3>
          <Skeleton className="h-4 w-64" />
        </div>
        <Skeleton className="h-9 w-full max-w-md" />
      </div>
    );
  if (settings.isError)
    return (
      <div className="flex flex-col gap-3 border-t pt-4">
        <h3 className="text-sm font-medium">{t("admin.knowledge.schedule")}</h3>
        <EmptyState
          variant="error"
          description={t("common.list.errorTitle")}
          onRetry={() => {
            void settings.refetch();
          }}
        />
      </div>
    );
  const row = settings.data;
  const values = draft ?? {
    frequency: row.curation_frequency,
    weekday: row.curation_weekday ?? 0,
    time: row.curation_time,
  };
  const cadence =
    values.frequency === "weekly"
      ? t("admin.knowledge.everyWeek", {
          weekday: weekdayLong(values.weekday, i18n.language),
          time: values.time,
        })
      : t("admin.knowledge.everyDay", { time: values.time });

  return (
    <div className="flex flex-col gap-3 border-t pt-4">
      <div className="flex flex-col gap-0.5">
        <h3 className="text-sm font-medium">{t("admin.knowledge.schedule")}</h3>
        <p className="text-muted-foreground text-xs">
          {cadence}
          {nextScheduled &&
            ` · ${t("admin.knowledge.nextScheduled", {
              when: formatWhen(nextScheduled, i18n.language) ?? "",
            })}`}
        </p>
      </div>
      <div className="flex flex-wrap items-start gap-4">
        <CadenceFields
          idPrefix="curation-cadence"
          values={values}
          disabled={readOnly}
          onChange={(next) => {
            setDraft(next);
          }}
        />
        {!readOnly && (
          <InlineSaveButton
            disabled={draft === null || save.isPending}
            onClick={() => {
              save.mutate({
                curation_frequency: values.frequency,
                curation_weekday: values.frequency === "weekly" ? values.weekday : null,
                curation_time: values.time,
              });
            }}
          />
        )}
      </div>
    </div>
  );
}

/** Save button padded by an empty label so it lines up with the input row of
 * the labeled fields beside it, not their whole label+input stack. */
function InlineSaveButton({ onClick, disabled }: { onClick: () => void; disabled: boolean }) {
  const { t } = useTranslation();
  return (
    <div className="ml-auto flex flex-col gap-1.5">
      <span aria-hidden className="text-sm leading-none select-none">
        &nbsp;
      </span>
      <Button size="sm" disabled={disabled} onClick={onClick}>
        {t("admin.platform.save")}
      </Button>
    </div>
  );
}

/** Shared frequency/weekday/time triple (curation schedule + backup window).
 * `idPrefix` keeps the time field's id/label unique — the two cadence blocks
 * share this screen, and a duplicate id would point both labels at the first. */
function CadenceFields({
  values,
  disabled,
  onChange,
  idPrefix,
}: {
  values: { frequency: "daily" | "weekly"; weekday: number; time: string };
  disabled: boolean;
  onChange: (next: { frequency: "daily" | "weekly"; weekday: number; time: string }) => void;
  idPrefix: string;
}) {
  const { t, i18n } = useTranslation();
  return (
    <>
      <div className="flex flex-col gap-1.5">
        <Label>{t("admin.knowledge.frequency")}</Label>
        <Select
          items={[
            { value: "daily", label: t("admin.knowledge.daily") },
            { value: "weekly", label: t("admin.knowledge.weekly") },
          ]}
          value={values.frequency}
          disabled={disabled}
          onValueChange={(value) => {
            if (value) onChange({ ...values, frequency: value });
          }}
        >
          <SelectTrigger size="sm" className="w-36">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="daily">{t("admin.knowledge.daily")}</SelectItem>
            <SelectItem value="weekly">{t("admin.knowledge.weekly")}</SelectItem>
          </SelectContent>
        </Select>
      </div>
      {values.frequency === "weekly" && (
        <div className="flex flex-col gap-1.5">
          <Label>{t("admin.knowledge.weekday")}</Label>
          <Select
            items={WEEKDAYS.map((day) => ({
              value: String(day),
              label: weekdayLong(day, i18n.language),
            }))}
            value={String(values.weekday)}
            disabled={disabled}
            onValueChange={(value) => {
              if (value) onChange({ ...values, weekday: Number(value) });
            }}
          >
            <SelectTrigger size="sm" className="w-36">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {WEEKDAYS.map((day) => (
                <SelectItem key={day} value={String(day)}>
                  {weekdayLong(day, i18n.language)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${idPrefix}-time`}>{t("admin.knowledge.time")}</Label>
        <Input
          id={`${idPrefix}-time`}
          className="w-24"
          value={values.time}
          disabled={disabled}
          onChange={(event) => {
            onChange({ ...values, time: event.target.value });
          }}
        />
      </div>
    </>
  );
}

function BackupCard() {
  const { t } = useTranslation();
  const session = useSession();
  const readOnly = !isOwner(session.user?.role);
  const settings = useQuery({
    queryKey: knowledgeKeys.backupSettings,
    queryFn: getBackupSettings,
  });
  // Kicked off alongside settings so the list is ready when the card unblocks.
  const snapshots = useQuery({
    queryKey: knowledgeKeys.backups,
    queryFn: listBackups,
    staleTime: LIVE_STALE_TIME,
  });

  if (settings.isPending) return <Skeleton className="h-40 w-full" />;
  if (settings.isError)
    return (
      <Card id="backups" className="scroll-mt-6 shadow-2xs">
        <CardHeader className="border-b">
          <CardTitle className="text-sm font-semibold">{t("admin.knowledge.backups")}</CardTitle>
        </CardHeader>
        <CardContent>
          <EmptyState
            variant="error"
            description={t("common.list.errorTitle")}
            onRetry={() => {
              void settings.refetch();
            }}
          />
        </CardContent>
      </Card>
    );
  const row = settings.data;

  return (
    <Card id="backups" className="scroll-mt-6 shadow-2xs">
      <CardHeader className="border-b">
        <CardTitle className="text-sm font-semibold">{t("admin.knowledge.backups")}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-6">
        <StorageSettings row={row} readOnly={readOnly} />
        <ScheduleSettings row={row} readOnly={readOnly} />
        <SnapshotsList snapshots={snapshots.data ?? []} />
      </CardContent>
    </Card>
  );
}

/** Save mutation shared by the two backup-settings sections: writes the patch,
 * refreshes the cached row, clears the section's draft, and toasts. */
function useSaveBackup(onSaved: () => void) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: patchBackupSettings,
    onSuccess: (fresh) => {
      queryClient.setQueryData(knowledgeKeys.backupSettings, fresh);
      onSaved();
      toast.success(t("admin.platform.saved"));
    },
    onError: (error) => void toastApiError(error, t("admin.platform.saveFailed")),
  });
}

/** Where snapshots land — destination URL + credential, saved on its own. */
function StorageSettings({ row, readOnly }: { row: BackupSettings; readOnly: boolean }) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<{ destination: string; credential: string } | null>(null);
  const save = useSaveBackup(() => {
    setDraft(null);
  });
  const values = draft ?? { destination: row.destination_url ?? "", credential: "" };

  return (
    <div className="flex flex-col gap-3">
      <h3 className="text-sm font-medium">{t("admin.knowledge.storageSection")}</h3>
      <div className="flex flex-wrap items-start gap-4">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="backup-destination">{t("admin.knowledge.destination")}</Label>
          <Input
            id="backup-destination"
            className="w-72"
            placeholder="s3://bucket/prefix"
            value={values.destination}
            disabled={readOnly}
            onChange={(event) => {
              setDraft({ ...values, destination: event.target.value });
            }}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="backup-credential">{t("admin.knowledge.credential")}</Label>
          <Input
            id="backup-credential"
            className="w-56"
            type="password"
            placeholder={row.credential_is_set ? "••••••••" : t("admin.knowledge.credentialHint")}
            value={values.credential}
            disabled={readOnly}
            onChange={(event) => {
              setDraft({ ...values, credential: event.target.value });
            }}
          />
        </div>
        {!readOnly && (
          <InlineSaveButton
            disabled={draft === null || save.isPending}
            onClick={() => {
              save.mutate({
                destination_url: values.destination || null,
                ...(values.credential ? { credential: values.credential } : {}),
              });
            }}
          />
        )}
      </div>
    </div>
  );
}

/** How often snapshots run + how many to keep, saved on its own. */
function ScheduleSettings({ row, readOnly }: { row: BackupSettings; readOnly: boolean }) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<{
    frequency: "daily" | "weekly";
    weekday: number;
    time: string;
    retention: string;
  } | null>(null);
  const save = useSaveBackup(() => {
    setDraft(null);
  });
  const values = draft ?? {
    frequency: row.frequency,
    weekday: row.weekday ?? 0,
    time: row.time,
    retention: String(row.retention_count),
  };

  return (
    <div className="flex flex-col gap-3 border-t pt-4">
      <h3 className="text-sm font-medium">{t("admin.knowledge.backupSchedule")}</h3>
      <div className="flex flex-wrap items-start gap-4">
        <CadenceFields
          idPrefix="backup-cadence"
          values={values}
          disabled={readOnly}
          onChange={(next) => {
            setDraft({ ...values, ...next });
          }}
        />
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="backup-retention">{t("admin.knowledge.retentionCount")}</Label>
          <Input
            id="backup-retention"
            type="number"
            min={1}
            className="w-24"
            value={values.retention}
            disabled={readOnly}
            onChange={(event) => {
              setDraft({ ...values, retention: event.target.value });
            }}
          />
        </div>
        {!readOnly && (
          <InlineSaveButton
            disabled={draft === null || save.isPending}
            onClick={() => {
              save.mutate({
                frequency: values.frequency,
                weekday: values.frequency === "weekly" ? values.weekday : null,
                time: values.time,
                retention_count: Number(values.retention) || row.retention_count,
              });
            }}
          />
        )}
      </div>
    </div>
  );
}

/** Newest three backups, with an expander for the rest. */
function SnapshotsList({ snapshots }: { snapshots: BackupSnapshot[] }) {
  const { t, i18n } = useTranslation();
  const [expanded, setExpanded] = useState(false);

  const shown = expanded ? snapshots : snapshots.slice(0, 3);

  return (
    <div className="flex flex-col gap-2 border-t pt-4">
      <h3 className="text-sm font-medium">{t("admin.knowledge.recentSnapshots")}</h3>
      {snapshots.length ? (
        <>
          <div className="divide-y rounded-lg border">
            {shown.map((snapshot) => (
              <SnapshotRow key={snapshot.id} snapshot={snapshot} locale={i18n.language} />
            ))}
          </div>
          {snapshots.length > 3 && (
            <Button
              variant="ghost"
              size="sm"
              className="self-end"
              onClick={() => {
                setExpanded((value) => !value);
              }}
            >
              {expanded
                ? t("admin.knowledge.showFewer")
                : t("admin.knowledge.showAll", { count: snapshots.length })}
            </Button>
          )}
        </>
      ) : (
        <p className="text-muted-foreground text-sm">{t("admin.knowledge.noSnapshots")}</p>
      )}
    </div>
  );
}

function SnapshotRow({ snapshot, locale }: { snapshot: BackupSnapshot; locale: string }) {
  const { t } = useTranslation();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const restore = useMutation({
    mutationFn: () => startRestore(snapshot.id),
    onSuccess: () => {
      setConfirmOpen(false);
      toast.success(t("admin.knowledge.restoreStarted"));
    },
    onError: (error) => void toastApiError(error, t("admin.knowledge.restoreFailed")),
  });

  return (
    <div className="hover:bg-muted/40 flex min-h-12 flex-wrap items-center gap-3 px-3 py-1.5 text-sm transition-colors">
      <Badge variant={runStateBadgeVariant(snapshot.state)}>
        {runStateLabel(snapshot.state, t)}
      </Badge>
      <span className="tabular-nums">{formatWhen(snapshot.started_at, locale)}</span>
      {snapshot.size_bytes !== null && (
        <span className="text-muted-foreground tabular-nums">
          {formatBytes(snapshot.size_bytes)}
        </span>
      )}
      {snapshot.error && <span className="text-destructive text-xs">{snapshot.error}</span>}
      {snapshot.state === "succeeded" && (
        <Button
          variant="outline"
          size="sm"
          className="ml-auto"
          onClick={() => {
            setTyped("");
            setConfirmOpen(true);
          }}
        >
          {t("admin.knowledge.restore")}
        </Button>
      )}
      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("admin.knowledge.restore")}</AlertDialogTitle>
            <AlertDialogDescription>{t("admin.knowledge.restoreHint")}</AlertDialogDescription>
          </AlertDialogHeader>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`restore-confirm-${String(snapshot.id)}`}>
              {t("admin.knowledge.restoreTypeToConfirm")}
            </Label>
            <Input
              id={`restore-confirm-${String(snapshot.id)}`}
              value={typed}
              onChange={(event) => {
                setTyped(event.target.value);
              }}
            />
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("admin.platform.cancel")}</AlertDialogCancel>
            <AlertDialogAction
              disabled={typed !== "restore" || restore.isPending}
              onClick={() => {
                restore.mutate();
              }}
            >
              {t("admin.knowledge.restore")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
