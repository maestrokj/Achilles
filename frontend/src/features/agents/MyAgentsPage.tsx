import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpRightIcon, BotIcon, CalendarClockIcon, PlayIcon, PlusIcon } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router-dom";

import { LIVE_STALE_TIME } from "@/api/freshness";
import { TruncatedText } from "@/components/TruncatedText";
import { EmptyState } from "@/components/list-controls/EmptyState";
import { FacetSelect } from "@/components/list-controls/FacetSelect";
import { SearchInput } from "@/components/list-controls/SearchInput";
import { PageError } from "@/components/PageError";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { formatDateTime, formatTokens } from "@/lib/format";
import { cn } from "@/lib/utils";

import { agentsQueryKeys, listAgents, patchAgent, runAgent } from "./api";
import { scheduleLabel } from "./format";
import { StatusChip } from "./StatusChip";
import { SCHEDULE_VALUES, STATUS_VALUES, type Agent } from "./types";

/** Above this count the toolbar (search + facets) appears; a short list needs
 * no filtering. */
const SEARCH_THRESHOLD = 4;

/** Web App · My agents — the owner's personal agents: enable, run, open a card.
 * Wireframe: web-app/_wireframes/my-agents.html. */
export function MyAgentsPage() {
  const { t, i18n } = useTranslation();
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<string[]>([]);
  const [scheduleFilter, setScheduleFilter] = useState<string[]>([]);
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const toggleFilter =
    (setter: React.Dispatch<React.SetStateAction<string[]>>) => (value: string) => {
      setter((prev) => (prev.includes(value) ? prev.filter((v) => v !== value) : [...prev, value]));
    };
  const isFiltered = search.trim() !== "" || statusFilter.length > 0 || scheduleFilter.length > 0;
  const resetFilters = () => {
    setSearch("");
    setStatusFilter([]);
    setScheduleFilter([]);
  };

  const query = useQuery({
    queryKey: agentsQueryKeys.list,
    queryFn: listAgents,
    staleTime: LIVE_STALE_TIME,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: agentsQueryKeys.list });
  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) => patchAgent(id, { enabled }),
    onSettled: invalidate,
  });
  const run = useMutation({
    mutationFn: (id: number) => runAgent(id),
    onSettled: invalidate,
  });

  const goCreate = () => {
    void navigate("/agents/new");
  };

  if (query.isPending) {
    return (
      <Shell onCreate={goCreate}>
        <div className="flex gap-3">
          <Skeleton className="h-24 flex-1 rounded-xl" />
          <Skeleton className="h-24 flex-1 rounded-xl" />
        </div>
        <div className="flex flex-col gap-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-28 w-full rounded-xl" />
          ))}
        </div>
      </Shell>
    );
  }
  if (query.isError) {
    return (
      <PageError
        className="px-6 py-8"
        description={t("agents.errors.loadFailed")}
        onRetry={() => void query.refetch()}
      />
    );
  }

  const { items, budget } = query.data;
  const visible = items.filter((agent) => {
    if (!agent.name.toLowerCase().includes(search.trim().toLowerCase())) return false;
    if (statusFilter.length > 0 && !statusFilter.includes(agent.status)) return false;
    // One side picked narrows the split; both (or neither) means no constraint.
    if (scheduleFilter.length === 1) {
      const scheduled = agent.schedule !== null;
      if (scheduleFilter[0] === "scheduled" && !scheduled) return false;
      if (scheduleFilter[0] === "manual" && scheduled) return false;
    }
    return true;
  });
  const scheduled = items.filter((agent) => agent.schedule !== null).length;
  const budgetPct =
    budget.limit !== null && budget.limit > 0 ? (budget.used / budget.limit) * 100 : undefined;

  return (
    <Shell onCreate={goCreate}>
      {items.length > 0 && (
        <div className="animate-in fade-in slide-in-from-bottom-1 flex gap-3 duration-500">
          <StatTile
            label={t("agents.stats.weekTokens")}
            hint={t("agents.stats.resets", {
              date: formatDateTime(budget.week_resets_at, i18n.language),
            })}
            value={
              <>
                <span>{formatTokens(budget.used, i18n.language)}</span>
                <span className="text-muted-foreground text-sm font-normal">
                  {" / "}
                  {budget.limit === null
                    ? t("agents.stats.noLimit")
                    : formatTokens(budget.limit, i18n.language)}
                </span>
              </>
            }
            gauge={budgetPct}
          />
          <StatTile
            label={t("agents.stats.agents")}
            hint={t("agents.stats.scheduled", { count: scheduled })}
            value={String(items.length)}
          />
        </div>
      )}

      {items.length > SEARCH_THRESHOLD && (
        <div className="flex flex-wrap items-center gap-2">
          <SearchInput
            value={search}
            onChange={setSearch}
            onClear={() => {
              setSearch("");
            }}
            placeholder={t("agents.searchPlaceholder")}
          />
          <FacetSelect
            label={t("agents.filters.status")}
            options={STATUS_VALUES.map((value) => ({ value, label: t(`agents.status.${value}`) }))}
            selected={statusFilter}
            onToggle={toggleFilter(setStatusFilter)}
          />
          <FacetSelect
            label={t("agents.filters.schedule")}
            options={SCHEDULE_VALUES.map((value) => ({
              value,
              label: t(`agents.filters.${value}`),
            }))}
            selected={scheduleFilter}
            onToggle={toggleFilter(setScheduleFilter)}
          />
        </div>
      )}

      {items.length === 0 ? (
        <EmptyState
          icon={BotIcon}
          title={t("agents.empty.title")}
          description={t("agents.empty.subtitle")}
          action={{ label: t("agents.create"), icon: PlusIcon, onClick: goCreate }}
        />
      ) : visible.length === 0 ? (
        <EmptyState filtered={isFiltered} onReset={resetFilters} />
      ) : (
        <div className="flex flex-col gap-3">
          {visible.map((agent) => (
            <AgentCard
              key={agent.id}
              agent={agent}
              onToggle={(enabled) => {
                toggle.mutate({ id: agent.id, enabled });
              }}
              onRun={() => {
                run.mutate(agent.id);
              }}
              runPending={run.isPending && run.variables === agent.id}
            />
          ))}
        </div>
      )}
    </Shell>
  );
}

/** Page frame: the header with the create action, then the routed content. */
function Shell({ children, onCreate }: { children: React.ReactNode; onCreate: () => void }) {
  const { t } = useTranslation();
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex max-w-3xl flex-col gap-6 px-6 py-8">
        <div className="flex items-center gap-3">
          <h1 className="flex-1 text-2xl font-semibold tracking-tight">{t("agents.title")}</h1>
          <Button onClick={onCreate}>
            <PlusIcon data-icon="inline-start" />
            {t("agents.create")}
          </Button>
        </div>
        {children}
      </div>
    </div>
  );
}

/** A compact metric tile — big value, muted label + hint, optional weekly gauge. */
function StatTile({
  value,
  label,
  hint,
  gauge,
}: {
  value: React.ReactNode;
  label: string;
  hint: string;
  gauge?: number;
}) {
  return (
    <Card className="flex-1 shadow-2xs">
      <CardContent className="flex flex-col gap-1">
        <p className="text-muted-foreground text-xs">{label}</p>
        <p className="flex items-baseline gap-0.5 text-2xl font-semibold tabular-nums">{value}</p>
        {gauge !== undefined && (
          <Progress
            value={Math.min(gauge, 100)}
            className={cn(
              "mt-1.5",
              gauge >= 100
                ? "[&_[data-slot=progress-indicator]]:bg-destructive"
                : gauge >= 80 && "[&_[data-slot=progress-indicator]]:bg-warning",
            )}
          />
        )}
        <p className="text-muted-foreground mt-0.5 text-xs">{hint}</p>
      </CardContent>
    </Card>
  );
}

/** The card's title link: one-line name with an overflow tooltip, and the
 * stretched link that opens the profile from anywhere on the card. */
function AgentName({ id, name }: { id: number; name: string }) {
  return (
    <TruncatedText
      render={
        <Link
          to={`/agents/${String(id)}`}
          className="text-[15px] font-medium after:absolute after:inset-0 after:content-[''] hover:underline"
        />
      }
    >
      {name}
    </TruncatedText>
  );
}

function AgentCard({
  agent,
  onToggle,
  onRun,
  runPending,
}: {
  agent: Agent;
  onToggle: (enabled: boolean) => void;
  onRun: () => void;
  runPending: boolean;
}) {
  const { t, i18n } = useTranslation();
  const locked = agent.admin_paused || agent.status === "model_missing";
  const active = agent.status === "active";

  return (
    <Card className="group/card hover:border-primary/40 hover:ring-primary/20 relative shadow-2xs transition-all hover:shadow-sm">
      <CardContent className="flex flex-col gap-3">
        <div className="flex items-start gap-3">
          {/* Status-tinted avatar: a calm identity mark for the card. */}
          <span
            className={cn(
              "mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-lg",
              active ? "bg-primary/10 text-primary" : "bg-secondary text-muted-foreground",
            )}
            aria-hidden
          >
            <BotIcon className="size-4.5" />
          </span>

          <div className="flex min-w-0 flex-1 flex-col gap-1">
            <div className="flex items-center gap-2.5">
              {/* Stretched link — the whole card opens the profile; the controls
                  below sit on their own layer and stay independently clickable. */}
              <AgentName id={agent.id} name={agent.name} />
              <StatusChip status={agent.status} className="shrink-0" />
              <ArrowUpRightIcon className="text-muted-foreground/40 size-4 shrink-0 opacity-0 transition-opacity group-hover/card:opacity-100" />
            </div>
            {agent.description && (
              <p className="text-muted-foreground line-clamp-2 text-sm">{agent.description}</p>
            )}
          </div>

          <Switch
            checked={agent.enabled}
            disabled={locked}
            onCheckedChange={onToggle}
            aria-label={t("agents.editor.enabled")}
            className="relative z-10 mt-1"
          />
        </div>

        <div className="border-border/60 text-muted-foreground flex items-center gap-3 border-t pt-3 text-xs">
          <span className="inline-flex items-center gap-1.5">
            <CalendarClockIcon className="size-3.5" />
            {scheduleLabel(agent.schedule, t, i18n.language)}
          </span>
          <span className="flex-1" />
          <Button
            variant="outline"
            size="sm"
            className="relative z-10"
            disabled={!active || runPending}
            onClick={onRun}
          >
            <PlayIcon data-icon="inline-start" />
            {t("agents.card.run")}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
