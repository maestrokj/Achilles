import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BotIcon } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { toast } from "@/lib/toast";

import { toastApiError } from "@/api/errors";
import { LIVE_STALE_TIME } from "@/api/freshness";
import { InfoHint } from "@/components/InfoHint";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/list-controls/EmptyState";
import { FacetSelect } from "@/components/list-controls/FacetSelect";
import { Pagination } from "@/components/list-controls/Pagination";
import { RowActionsMenu } from "@/components/list-controls/RowActions";
import { SearchInput } from "@/components/list-controls/SearchInput";
import { TableSkeleton } from "@/components/list-controls/TableSkeleton";
import { buildListQuery, useListState } from "@/components/list-controls/useListState";
import {
  DataTable,
  ROW_LINK_ABOVE,
  ROW_LINK_ROW,
  RowLink,
  TableFrame,
} from "@/components/list-controls/DataTable";
import { TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDuration, formatTokens, formatWhen } from "@/lib/format";

import {
  adminListAgents,
  adminSetPause,
  agentsQueryKeys,
  getAgentLimits,
  patchAgentLimits,
} from "../api";
import { scheduleLabel } from "../format";
import { StatusChip } from "../StatusChip";
import { SCHEDULE_VALUES, STATUS_VALUES, type AdminAgent, type RunState } from "../types";
import { PauseConfirmDialog } from "./PauseConfirmDialog";

/** Admin · All agents: platform run limits + the registry with the pause lever.
 * Wireframe: admin-panel/_wireframes/agents.html. The Status facet lists the
 * derived statuses (OR-combined server-side); the Schedule facet toggles the
 * scheduled/manual split. The owner facet has no backend support (agents.html). */
export function AdminAgentsPage() {
  const { t } = useTranslation();
  const [pauseTarget, setPauseTarget] = useState<AdminAgent | null>(null);
  const queryClient = useQueryClient();
  const list = useListState(["status", "schedule"]);

  const statusSelected = list.facets["status"].filter((value) =>
    (STATUS_VALUES as string[]).includes(value),
  );
  const scheduleSelected = list.facets["schedule"];
  // Both (or neither) sides picked = no constraint; exactly one narrows the split.
  const scheduled = scheduleSelected.length === 1 ? scheduleSelected[0] === "scheduled" : undefined;
  const listQuery = buildListQuery(list);

  const query = useQuery({
    queryKey: agentsQueryKeys.adminList(listQuery),
    queryFn: () =>
      adminListAgents({
        q: list.q || undefined,
        status: statusSelected.length > 0 ? statusSelected : undefined,
        scheduled,
        page: list.page,
        per_page: list.perPage,
      }),
    // Filter/search/page changes keep the previous rows on screen (keep-previous).
    placeholderData: keepPreviousData,
    staleTime: LIVE_STALE_TIME,
  });

  const pause = useMutation({
    mutationFn: ({ id, paused }: { id: number; paused: boolean }) => adminSetPause(id, paused),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["agents", "admin"] }),
  });

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
      <div className="flex items-center gap-1.5">
        <h1 className="text-2xl font-semibold tracking-tight">{t("admin.nav.agents")}</h1>
        <InfoHint text={t("admin.agents.intro")} />
      </div>

      <LimitsCard />

      <div className="flex flex-wrap items-center gap-2">
        <SearchInput
          value={list.input}
          onChange={list.setInput}
          onClear={list.clearSearch}
          placeholder={t("admin.agents.table.searchPlaceholder")}
        />
        <FacetSelect
          label={t("admin.agents.table.status")}
          options={STATUS_VALUES.map((value) => ({
            value,
            label: t(`agents.status.${value}`),
          }))}
          selected={statusSelected}
          onToggle={(value) => {
            list.toggleFacet("status", value);
          }}
        />
        <FacetSelect
          label={t("admin.agents.table.schedule")}
          options={SCHEDULE_VALUES.map((value) => ({
            value,
            label: t(`admin.agents.table.facets.schedule.${value}`),
          }))}
          selected={scheduleSelected}
          onToggle={(value) => {
            list.toggleFacet("schedule", value);
          }}
        />
      </div>

      {query.isPending ? (
        <TableSkeleton cols={6} />
      ) : query.isError ? (
        <EmptyState
          variant="error"
          description={t("agents.errors.loadFailed")}
          onRetry={() => {
            void query.refetch();
          }}
        />
      ) : query.data.items.length === 0 ? (
        <EmptyState
          filtered={list.isFiltered}
          onReset={list.clearFilters}
          icon={BotIcon}
          title={t("admin.agents.table.emptyTitle")}
          description={t("admin.agents.table.emptyHint")}
        />
      ) : (
        <>
          <TableFrame>
            <DataTable>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("admin.agents.table.agent")}</TableHead>
                  <TableHead>{t("admin.agents.table.owner")}</TableHead>
                  <TableHead>{t("admin.agents.table.status")}</TableHead>
                  <TableHead>{t("admin.agents.table.schedule")}</TableHead>
                  <TableHead>{t("admin.agents.table.lastRun")}</TableHead>
                  <TableHead className="w-10" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {query.data.items.map((agent) => (
                  <AgentRow key={agent.id} agent={agent} onPause={setPauseTarget} />
                ))}
              </TableBody>
            </DataTable>
          </TableFrame>
          <Pagination
            page={query.data.page}
            perPage={list.perPage}
            total={query.data.total}
            onPageChange={list.setPage}
            onPerPageChange={list.setPerPage}
          />
        </>
      )}

      <PauseConfirmDialog
        paused={pauseTarget?.admin_paused ?? false}
        open={pauseTarget !== null}
        onOpenChange={(open) => {
          if (!open) setPauseTarget(null);
        }}
        onConfirm={() => {
          if (pauseTarget) {
            pause.mutate({ id: pauseTarget.id, paused: !pauseTarget.admin_paused });
          }
          setPauseTarget(null);
        }}
      />
    </div>
  );
}

/** Run-state accent — a small dot so the last run reads at a glance. */
const RUN_DOT: Record<RunState, string> = {
  queued: "bg-muted-foreground/40",
  running: "bg-primary",
  succeeded: "bg-success",
  failed: "bg-destructive",
  skipped: "bg-warning",
};

function AgentRow({ agent, onPause }: { agent: AdminAgent; onPause: (agent: AdminAgent) => void }) {
  const { t, i18n } = useTranslation();
  const profilePath = `/admin/agents/${String(agent.id)}`;
  const run = agent.last_run;
  const runMeta =
    run &&
    [
      t(`agents.runState.${run.state}`),
      run.duration_seconds === null ? null : formatDuration(run.duration_seconds, i18n.language),
      formatTokens(run.tokens_used, i18n.language),
    ]
      .filter(Boolean)
      .join(" · ");

  return (
    <TableRow className={`${ROW_LINK_ROW} h-12`}>
      <TableCell className="max-w-[16rem]">
        <RowLink to={profilePath}>{agent.name}</RowLink>
      </TableCell>
      <TableCell className={`${ROW_LINK_ABOVE} max-w-[16rem]`}>
        <Link
          to={`/admin/users/${String(agent.owner.id)}`}
          title={agent.owner.display_name ?? agent.owner.email}
          className="block truncate text-sm hover:underline"
        >
          {agent.owner.display_name ?? agent.owner.email}
        </Link>
        <span title={agent.owner.email} className="text-muted-foreground block truncate text-xs">
          {agent.owner.email}
        </span>
      </TableCell>
      <TableCell>
        <StatusChip status={agent.status} />
      </TableCell>
      <TableCell className="text-muted-foreground text-sm">
        {scheduleLabel(agent.schedule, t, i18n.language)}
      </TableCell>
      <TableCell>
        {run ? (
          <div className="flex flex-col gap-0.5">
            <span className="text-sm">
              {formatWhen(run.finished_at ?? agent.created_at, i18n.language)}
            </span>
            <span className="text-muted-foreground flex items-center gap-1.5 text-xs">
              <span aria-hidden="true" className={`size-1.5 rounded-full ${RUN_DOT[run.state]}`} />
              {runMeta}
            </span>
          </div>
        ) : (
          <span className="text-muted-foreground text-sm">{t("agents.card.never")}</span>
        )}
      </TableCell>
      <TableCell className={ROW_LINK_ABOVE}>
        <RowActionsMenu label={t("admin.agents.table.rowMenu")}>
          <DropdownMenuItem render={<Link to={profilePath} />}>
            {t("admin.agents.actions.openProfile")}
          </DropdownMenuItem>
          <DropdownMenuItem
            onClick={() => {
              onPause(agent);
            }}
          >
            {agent.admin_paused
              ? t("admin.agents.actions.unpause")
              : t("admin.agents.actions.pause")}
          </DropdownMenuItem>
        </RowActionsMenu>
      </TableCell>
    </TableRow>
  );
}

function LimitsCard() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: agentsQueryKeys.limits, queryFn: getAgentLimits });
  const [draft, setDraft] = useState<{ iteration_cap: string; max_concurrency: string } | null>(
    null,
  );

  const save = useMutation({
    mutationFn: () =>
      patchAgentLimits({
        iteration_cap: Number(draft?.iteration_cap),
        max_concurrency: Number(draft?.max_concurrency),
      }),
    onSuccess: async () => {
      setDraft(null);
      await queryClient.invalidateQueries({ queryKey: agentsQueryKeys.limits });
      toast.success(t("admin.agents.limits.saved"));
    },
    onError: (error) => void toastApiError(error, t("admin.platform.saveFailed")),
  });

  if (query.isPending)
    return (
      <Card className="shadow-2xs">
        <CardContent className="py-6">
          <Skeleton className="h-32 w-full" />
        </CardContent>
      </Card>
    );
  if (query.isError)
    return (
      <Card className="shadow-2xs">
        <CardContent className="py-6">
          <EmptyState
            variant="error"
            onRetry={() => {
              void query.refetch();
            }}
          />
        </CardContent>
      </Card>
    );

  const limits = query.data;
  const values = draft ?? {
    iteration_cap: String(limits.iteration_cap),
    max_concurrency: String(limits.max_concurrency),
  };
  const valid = Number(values.iteration_cap) > 0 && Number(values.max_concurrency) > 0;

  return (
    <Card className="shadow-2xs">
      <CardHeader className="border-b">
        <CardTitle className="flex items-center gap-1.5 text-sm font-semibold">
          {t("admin.agents.limits.title")}
          <InfoHint text={t("admin.agents.limits.subtitle")} />
        </CardTitle>
      </CardHeader>
      <CardContent>
        {/* Two setting rows: label + hint-on-hover on the left, the value on the
            right, parted by a hairline — the layout the wireframe intends. */}
        <div className="divide-border/70 divide-y">
          <SettingRow
            id="limit-cap"
            label={t("admin.agents.limits.iterationCap")}
            hint={t("admin.agents.limits.iterationCapHint")}
            value={values.iteration_cap}
            onChange={(next) => {
              setDraft({ ...values, iteration_cap: next });
            }}
          />
          <SettingRow
            id="limit-concurrency"
            label={t("admin.agents.limits.maxConcurrency")}
            hint={t("admin.agents.limits.maxConcurrencyHint")}
            value={values.max_concurrency}
            onChange={(next) => {
              setDraft({ ...values, max_concurrency: next });
            }}
          />
        </div>
        <div className="border-border/70 mt-4 flex items-center justify-between gap-4 border-t pt-4">
          <p className="text-muted-foreground text-xs">{t("admin.agents.limits.footnote")}</p>
          <Button
            size="sm"
            disabled={draft === null || !valid || save.isPending}
            onClick={() => {
              save.mutate();
            }}
          >
            {t("admin.agents.limits.save")}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function SettingRow({
  id,
  label,
  hint,
  value,
  onChange,
}: {
  id: string;
  label: string;
  hint: string;
  value: string;
  onChange: (next: string) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-6 py-3 first:pt-0 last:pb-0">
      <Label htmlFor={id} className="flex items-center gap-1.5 text-sm font-medium">
        {label}
        <InfoHint text={hint} />
      </Label>
      <Input
        id={id}
        type="number"
        min={1}
        className="w-20 text-right tabular-nums"
        value={value}
        onChange={(event) => {
          onChange(event.target.value);
        }}
      />
    </div>
  );
}
