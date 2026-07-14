import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DatabaseIcon, PlugIcon, TriangleAlertIcon } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { toastApiError } from "@/api/errors";
import { LIVE_STALE_TIME } from "@/api/freshness";
import { toast } from "@/lib/toast";

import { BuildYourOwnCard } from "@/components/BuildYourOwnCard";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { TruncatedText } from "@/components/TruncatedText";
import {
  DataTable,
  ROW_LINK_ABOVE,
  ROW_LINK_ROW,
  RowLink,
  SortableHead,
  TableFrame,
} from "@/components/list-controls/DataTable";
import { EmptyState } from "@/components/list-controls/EmptyState";
import { RowActionsMenu } from "@/components/list-controls/RowActions";
import { useClientSort, type SortAccessors } from "@/components/list-controls/useClientSort";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { EmbedderPill } from "@/features/admin/ai/EmbedderPill";
import {
  getPlatformSettings,
  patchPlatformSettings,
  platformKeys,
} from "@/features/admin/platform/api";
import type { PlatformSettingsPatch } from "@/features/admin/platform/types";
import { WEEKDAYS, formatDuration, formatTokens, formatWhen, weekdayLong } from "@/lib/format";

import {
  cancelSync,
  harvesterKeys,
  listDeadLetters,
  listSources,
  patchSource,
  retryDeadLetters,
  startSync,
  syncAll,
} from "./api";
import { stateBadgeVariant } from "./badges";
import { ConnectWizard } from "./ConnectWizard";
import { MINUTES_PER_DAY, minuteToTime, timeToMinute } from "./timeOfDay";
import type { Source, SourceLastRun } from "./types";

const SYNC_INTERVAL_CHOICES = [15, 30, 60, 180, 360, 720, 1440];

/** Admin · Harvester: global collection settings + the sources hub.
 * Wireframe: admin-panel/_wireframes/harvester.html. */
export function HarvesterPage() {
  const { t } = useTranslation();
  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
      <h1 className="text-2xl font-semibold tracking-tight">{t("admin.nav.harvester")}</h1>
      <GlobalSettingsCard />
      <SourcesCard />
      <BuildYourOwnCard
        icon={PlugIcon}
        title={t("admin.harvester.custom.title")}
        description={t("admin.harvester.custom.desc")}
        doc="harvester/_workzone/connectors.html#custom"
      />
    </div>
  );
}

function GlobalSettingsCard() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const settings = useQuery({ queryKey: platformKeys.settings, queryFn: getPlatformSettings });

  const save = useMutation({
    mutationFn: patchPlatformSettings,
    onSuccess: (fresh) => {
      queryClient.setQueryData(platformKeys.settings, fresh);
      toast.success(t("admin.platform.saved"));
    },
    onError: (error) => void toastApiError(error, t("admin.platform.saveFailed")),
  });

  if (settings.isPending || settings.isError) return <Skeleton className="h-24 w-full" />;
  const row = settings.data;

  return (
    <Card className="shadow-2xs">
      <CardHeader className="border-b">
        <CardTitle className="text-sm font-semibold">
          {t("admin.harvester.globalSettings")}
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col items-start gap-5">
        <div className="flex flex-col gap-1.5">
          <Label>{t("admin.harvester.embedder")}</Label>
          <EmbedderPill />
        </div>
        <div aria-hidden="true" className="bg-border/60 h-px w-full" />
        {/* Draft + explicit save: the admin sets the cadence, then commits — no
         * write on every dropdown flick. Remount (key) on the server value so a
         * successful save resets the field. */}
        <SyncIntervalField
          key={`interval-${String(row.sync_interval_minutes)}`}
          value={row.sync_interval_minutes}
          onSave={save.mutate}
          pending={save.isPending}
        />
        <div aria-hidden="true" className="bg-border/60 h-px w-full" />
        <ReconcileWindowField
          key={`reconcile-${String(row.reconcile_minute_of_week)}`}
          value={row.reconcile_minute_of_week}
          onSave={save.mutate}
          pending={save.isPending}
        />
      </CardContent>
    </Card>
  );
}

/** Incremental cadence: pick an interval, then Save — the button surfaces only
 * once the draft diverges from what is stored. */
function SyncIntervalField({
  value,
  onSave,
  pending,
}: {
  value: number;
  onSave: (patch: PlatformSettingsPatch) => void;
  pending: boolean;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState(String(value));
  const dirty = draft !== String(value);

  return (
    <div className="flex flex-col gap-1.5">
      <Label>{t("admin.harvester.syncInterval")}</Label>
      <div className="flex items-center gap-2">
        <Select
          items={SYNC_INTERVAL_CHOICES.map((minutes) => ({
            value: String(minutes),
            label:
              minutes < 60
                ? t("admin.harvester.everyMinutes", { count: minutes })
                : t("admin.harvester.everyHours", { count: minutes / 60 }),
          }))}
          value={draft}
          onValueChange={(next) => {
            if (next) setDraft(next);
          }}
        >
          <SelectTrigger size="sm" className="w-44">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {SYNC_INTERVAL_CHOICES.map((minutes) => (
              <SelectItem key={minutes} value={String(minutes)}>
                {minutes < 60
                  ? t("admin.harvester.everyMinutes", { count: minutes })
                  : t("admin.harvester.everyHours", { count: minutes / 60 })}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button
          size="sm"
          disabled={!dirty || pending}
          onClick={() => {
            onSave({ sync_interval_minutes: Number(draft) });
          }}
        >
          {t("admin.platform.save")}
        </Button>
      </div>
    </div>
  );
}

/** Full-sweep window: weekday + time draft, saved as one minute-of-week. The
 * time is validated live so Save appears only for a real value; blur snaps a
 * malformed entry back to the stored time. */
function ReconcileWindowField({
  value,
  onSave,
  pending,
}: {
  value: number;
  onSave: (patch: PlatformSettingsPatch) => void;
  pending: boolean;
}) {
  const { t, i18n } = useTranslation();
  const serverMinute = value % MINUTES_PER_DAY;
  const [draftDay, setDraftDay] = useState(Math.floor(value / MINUTES_PER_DAY));
  const [timeStr, setTimeStr] = useState(minuteToTime(serverMinute));

  const draftMinute = timeToMinute(timeStr);
  const draft = draftMinute === null ? null : draftDay * MINUTES_PER_DAY + draftMinute;
  const dirty = draft !== null && draft !== value;

  return (
    <div className="flex flex-col gap-1.5">
      <Label>{t("admin.harvester.reconcileWindow")}</Label>
      <div className="flex items-center gap-2">
        <Select
          items={WEEKDAYS.map((day) => ({
            value: String(day),
            label: weekdayLong(day, i18n.language),
          }))}
          value={String(draftDay)}
          onValueChange={(next) => {
            if (next) setDraftDay(Number(next));
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
        <Input
          className="w-20"
          value={timeStr}
          aria-label={t("admin.harvester.reconcileTime")}
          placeholder="03:00"
          onChange={(event) => {
            setTimeStr(event.target.value);
          }}
          onBlur={() => {
            if (timeToMinute(timeStr) === null) setTimeStr(minuteToTime(serverMinute));
          }}
        />
        <Button
          size="sm"
          disabled={!dirty || pending}
          onClick={() => {
            if (draft !== null) onSave({ reconcile_minute_of_week: draft });
          }}
        >
          {t("admin.platform.save")}
        </Button>
      </div>
    </div>
  );
}

type SortKey = "name" | "state" | "health" | "entities" | "last_sync";

const SORT_VALUE: SortAccessors<Source, SortKey> = {
  name: (source) => source.name.toLowerCase(),
  state: (source) => source.state,
  health: (source) => source.health,
  entities: (source) => source.entity_count,
  last_sync: (source) => (source.last_sync_at ? Date.parse(source.last_sync_at) : 0),
};

function SourcesCard() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [wizardOpen, setWizardOpen] = useState(false);
  const [syncAllOpen, setSyncAllOpen] = useState(false);
  const sources = useQuery({
    queryKey: harvesterKeys.sources,
    queryFn: listSources,
    staleTime: LIVE_STALE_TIME,
  });
  // Few sources per org — sorting is client-side, newest sync first by default.
  const {
    sorted,
    sort,
    toggle: toggleSort,
  } = useClientSort(sources.data ?? [], SORT_VALUE, { key: "last_sync", desc: true });

  const refresh = () => queryClient.invalidateQueries({ queryKey: harvesterKeys.sources });
  const syncEverything = useMutation({
    mutationFn: syncAll,
    onSuccess: (result) => {
      void refresh();
      toast.success(t("admin.harvester.syncAllStarted", { count: result.run_ids.length }));
    },
    onError: (error) => void toastApiError(error, t("admin.harvester.syncFailed")),
  });

  const anyActive = (sources.data ?? []).some(
    (source) => source.state === "active" && source.health !== "syncing",
  );

  return (
    <Card className="shadow-2xs">
      <CardHeader className="border-b">
        <CardTitle className="text-sm font-semibold">{t("admin.harvester.sources")}</CardTitle>
        <CardAction className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={!anyActive || syncEverything.isPending}
            onClick={() => {
              setSyncAllOpen(true);
            }}
          >
            {t("admin.harvester.syncAll")}
          </Button>
          <Button
            size="sm"
            onClick={() => {
              setWizardOpen(true);
            }}
          >
            {t("admin.harvester.addSource")}
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent>
        {sources.isPending ? (
          <Skeleton className="h-32 w-full" />
        ) : sources.isError ? (
          <EmptyState
            variant="error"
            description={t("common.list.errorTitle")}
            onRetry={() => {
              void sources.refetch();
            }}
          />
        ) : sources.data.length === 0 ? (
          <EmptyState
            icon={DatabaseIcon}
            title={t("admin.harvester.emptyTitle")}
            description={t("admin.harvester.emptyHint")}
          />
        ) : (
          <TableFrame variant="card">
            <DataTable>
              <TableHeader>
                <TableRow>
                  <SortableHead
                    label={t("admin.harvester.table.source")}
                    sortKey="name"
                    sort={sort}
                    onToggle={toggleSort}
                  />
                  <SortableHead
                    label={t("admin.harvester.table.state")}
                    sortKey="state"
                    sort={sort}
                    onToggle={toggleSort}
                  />
                  <SortableHead
                    label={t("admin.harvester.table.health")}
                    sortKey="health"
                    sort={sort}
                    onToggle={toggleSort}
                  />
                  <SortableHead
                    label={t("admin.harvester.table.entities")}
                    sortKey="entities"
                    sort={sort}
                    onToggle={toggleSort}
                    align="center"
                  />
                  <SortableHead
                    label={t("admin.harvester.table.lastSync")}
                    sortKey="last_sync"
                    sort={sort}
                    onToggle={toggleSort}
                  />
                  <TableHead className="w-10" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {sorted.map((source) => (
                  <SourceRow key={source.id} source={source} />
                ))}
              </TableBody>
            </DataTable>
          </TableFrame>
        )}
      </CardContent>
      <ConnectWizard open={wizardOpen} onOpenChange={setWizardOpen} />
      <ConfirmDialog
        open={syncAllOpen}
        onOpenChange={setSyncAllOpen}
        title={t("admin.harvester.confirmSync.allTitle")}
        description={t("admin.harvester.confirmSync.allBody")}
        confirmLabel={t("admin.harvester.confirmSync.allConfirm")}
        pending={syncEverything.isPending}
        onConfirm={() => {
          syncEverything.mutate(undefined, {
            onSuccess: () => {
              setSyncAllOpen(false);
            },
          });
        }}
      />
    </Card>
  );
}

/** Health dot tone — the platform's observation axis (error / waiting / fine). */
const HEALTH_DOT: Record<Source["health"], string> = {
  idle: "bg-success",
  queued: "bg-warning",
  syncing: "bg-info",
  error: "bg-destructive",
};

/** The second line of "Last sync": progress while running, duration + delta or
 * the error after (harvester.html run cell). */
function LastRunMeta({ run }: { run: SourceLastRun }) {
  const { t, i18n } = useTranslation();
  if (run.state === "running" || run.state === "queued") {
    if (run.progress_done === null || run.progress_total === null) return null;
    return (
      <span className="text-muted-foreground text-xs tabular-nums">
        {t("admin.harvester.progress", {
          done: formatTokens(run.progress_done, i18n.language),
          total: formatTokens(run.progress_total, i18n.language),
        })}
      </span>
    );
  }
  if (run.state === "failed" && run.error) {
    return (
      <TruncatedText className="text-destructive/90 max-w-52 text-xs font-normal">
        {run.error}
      </TruncatedText>
    );
  }
  const parts = [
    run.duration_seconds === null ? null : formatDuration(run.duration_seconds, i18n.language),
    run.progress_done === null ? null : `+${formatTokens(run.progress_done, i18n.language)}`,
  ].filter((part) => part !== null);
  if (parts.length === 0) return null;
  return <span className="text-muted-foreground text-xs tabular-nums">{parts.join(" · ")}</span>;
}

function SourceRow({ source }: { source: Source }) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [syncConfirmOpen, setSyncConfirmOpen] = useState(false);
  const refresh = () => queryClient.invalidateQueries({ queryKey: harvesterKeys.sources });
  const onError = (error: unknown) => void toastApiError(error, t("admin.harvester.syncFailed"));

  const sync = useMutation({
    mutationFn: () => startSync(source.id, "incremental"),
    onSuccess: () => void refresh(),
    onError,
  });
  const cancel = useMutation({
    mutationFn: () => cancelSync(source.id),
    onSuccess: () => void refresh(),
    onError,
  });
  const pause = useMutation({
    mutationFn: (state: "active" | "paused") => patchSource(source.id, { state }),
    onSuccess: () => void refresh(),
    onError,
  });

  const syncing = source.health === "syncing";
  const lastSync = formatWhen(source.last_sync_at, i18n.language);
  return (
    <TableRow className={ROW_LINK_ROW}>
      <TableCell className="max-w-[16rem]">
        <div className="flex min-w-0 flex-col gap-0.5">
          <RowLink to={`/admin/harvester/sources/${String(source.id)}`}>{source.name}</RowLink>
          <span className="text-muted-foreground truncate text-xs">{source.connector_type}</span>
        </div>
      </TableCell>
      <TableCell>
        <Badge variant={stateBadgeVariant(source.state)}>
          {t(`admin.harvester.states.${source.state}`)}
        </Badge>
      </TableCell>
      <TableCell>
        <span className="text-muted-foreground flex items-center gap-1.5 text-xs">
          {syncing ? (
            <Spinner className="border-t-muted-foreground size-3" />
          ) : (
            <span
              aria-hidden="true"
              className={`size-1.5 rounded-full ${HEALTH_DOT[source.health]}`}
            />
          )}
          {t(`admin.harvester.health.${source.health}`)}
        </span>
      </TableCell>
      <TableCell className="text-center">
        <span className="text-muted-foreground text-xs tabular-nums">
          {formatTokens(source.entity_count, i18n.language)}
        </span>
      </TableCell>
      <TableCell>
        <div className="flex flex-col gap-1">
          {lastSync ? (
            <span className="text-foreground/80 text-xs tabular-nums">{lastSync}</span>
          ) : (
            <span className="text-muted-foreground text-xs">
              {t("admin.harvester.neverSynced")}
            </span>
          )}
          {(source.last_run || source.dlq_count > 0) && (
            <span className={`${ROW_LINK_ABOVE} flex items-center gap-2`}>
              {source.last_run && <LastRunMeta run={source.last_run} />}
              {source.dlq_count > 0 && <DlqPopover source={source} />}
            </span>
          )}
        </div>
      </TableCell>
      <TableCell className={ROW_LINK_ABOVE}>
        <RowActionsMenu label={t("admin.harvester.rowMenu")}>
          {syncing ? (
            <DropdownMenuItem
              onClick={() => {
                cancel.mutate();
              }}
            >
              {t("admin.harvester.cancelRun")}
            </DropdownMenuItem>
          ) : (
            <>
              <DropdownMenuItem
                disabled={source.state !== "active"}
                onClick={() => {
                  setSyncConfirmOpen(true);
                }}
              >
                {t("admin.harvester.sync")}
              </DropdownMenuItem>
              <DropdownMenuItem
                disabled={source.state === "disconnected"}
                onClick={() => {
                  pause.mutate(source.state === "paused" ? "active" : "paused");
                }}
              >
                {source.state === "paused"
                  ? t("admin.harvester.resume")
                  : t("admin.harvester.pause")}
              </DropdownMenuItem>
            </>
          )}
          <DropdownMenuItem
            onClick={() => {
              void navigate(`/admin/harvester/sources/${String(source.id)}`);
            }}
          >
            {t("admin.harvester.openCard")}
          </DropdownMenuItem>
        </RowActionsMenu>
        <ConfirmDialog
          open={syncConfirmOpen}
          onOpenChange={setSyncConfirmOpen}
          title={t("admin.harvester.confirmSync.oneTitle")}
          description={t("admin.harvester.confirmSync.oneBody", { name: source.name })}
          confirmLabel={t("admin.harvester.confirmSync.oneConfirm")}
          pending={sync.isPending}
          onConfirm={() => {
            sync.mutate(undefined, {
              onSuccess: () => {
                setSyncConfirmOpen(false);
              },
            });
          }}
        />
      </TableCell>
    </TableRow>
  );
}

function DlqPopover({ source }: { source: Source }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const letters = useQuery({
    queryKey: harvesterKeys.deadLetters(source.id),
    queryFn: () => listDeadLetters(source.id),
    enabled: open,
    staleTime: LIVE_STALE_TIME,
  });
  const retry = useMutation({
    mutationFn: () => retryDeadLetters(source.id),
    onSuccess: () => {
      setOpen(false);
      void queryClient.invalidateQueries({ queryKey: harvesterKeys.sources });
      toast.success(t("admin.harvester.retryStarted"));
    },
    onError: (error) => void toastApiError(error, t("admin.harvester.syncFailed")),
  });

  const byReason = new Map<string, number>();
  for (const letter of letters.data ?? []) {
    byReason.set(letter.reason, (byReason.get(letter.reason) ?? 0) + 1);
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={
          <Button
            variant="ghost"
            size="xs"
            aria-label={t("admin.harvester.dlqPill", { count: source.dlq_count })}
            className="text-warning hover:bg-warning/10 hover:text-warning -ml-0.5 gap-1 rounded-full px-1.5 font-medium tabular-nums"
          />
        }
      >
        <TriangleAlertIcon aria-hidden="true" className="size-3.5" />
        {source.dlq_count}
      </PopoverTrigger>
      <PopoverContent className="flex w-64 flex-col gap-2">
        {letters.isPending ? (
          <Skeleton className="h-10 w-full" />
        ) : (
          [...byReason.entries()].map(([reason, count]) => (
            <div key={reason} className="flex justify-between text-sm">
              <span>{reason}</span>
              <span className="text-muted-foreground">× {count}</span>
            </div>
          ))
        )}
        <Button
          size="sm"
          disabled={retry.isPending}
          onClick={() => {
            retry.mutate();
          }}
        >
          {t("admin.harvester.retryFailed")}
        </Button>
      </PopoverContent>
    </Popover>
  );
}
