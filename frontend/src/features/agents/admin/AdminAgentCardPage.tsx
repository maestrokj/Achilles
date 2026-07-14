import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BotIcon,
  CalendarClockIcon,
  CpuIcon,
  GaugeIcon,
  HistoryIcon,
  LockIcon,
  PauseIcon,
  PlayIcon,
  SlidersHorizontalIcon,
  UserRoundIcon,
  WrenchIcon,
  type LucideIcon,
} from "lucide-react";
import { type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";

import { LIVE_STALE_TIME } from "@/api/freshness";
import { BackLink } from "@/components/BackLink";
import { InfoHint } from "@/components/InfoHint";
import { PageError } from "@/components/PageError";
import { PageSkeleton } from "@/components/PageSkeleton";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { formatTokens } from "@/lib/format";
import { cn } from "@/lib/utils";

import {
  adminGetAgent,
  adminListRuns,
  adminSetPause,
  agentsQueryKeys,
  getAgentOptions,
} from "../api";
import { scheduleLabel } from "../format";
import { RunJournal } from "../RunJournal";
import { StatusChip } from "../StatusChip";
import { PauseConfirmDialog } from "./PauseConfirmDialog";

/** Admin · Agent profile: read-only configuration + the pause toggle.
 * Wireframe: admin-panel/_wireframes/agent-card.html. */
export function AdminAgentCardPage() {
  const { agentId } = useParams();
  const id = Number(agentId);
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);

  const query = useQuery({
    queryKey: agentsQueryKeys.adminAgent(id),
    queryFn: () => adminGetAgent(id),
    staleTime: LIVE_STALE_TIME,
  });
  const optionsQuery = useQuery({
    queryKey: agentsQueryKeys.options,
    queryFn: getAgentOptions,
  });

  const pause = useMutation({
    mutationFn: (paused: boolean) => adminSetPause(id, paused),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["agents", "admin"] }),
  });

  if (query.isPending) return <PageSkeleton />;
  if (query.isError) {
    return (
      <PageError description={t("agents.errors.loadFailed")} onRetry={() => void query.refetch()} />
    );
  }

  const agent = query.data;
  const budget = agent.owner_budget;
  const spendPct =
    budget.limit !== null && budget.limit > 0 ? (budget.used / budget.limit) * 100 : undefined;

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
      <BackLink to="/admin/agents" label={t("admin.nav.agents")} />
      <header className="flex items-start gap-4">
        <div
          aria-hidden="true"
          className="bg-muted text-muted-foreground grid size-12 shrink-0 place-items-center rounded-2xl"
        >
          <BotIcon className="size-5" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
            <h1 className="truncate text-2xl font-semibold tracking-tight">{agent.name}</h1>
            <span className="inline-flex items-center gap-1">
              <StatusChip status={agent.status} />
              {agent.admin_paused && (
                <InfoHint
                  text={t("admin.agents.card.pausedBanner")}
                  className="text-destructive/80 hover:text-destructive inline-flex align-middle transition-colors"
                />
              )}
            </span>
          </div>
          <div className="text-muted-foreground mt-1.5 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-sm">
            <UserRoundIcon className="size-3.5 shrink-0" aria-hidden="true" />
            <Link
              to={`/admin/users/${String(agent.owner.id)}`}
              className="hover:text-foreground truncate transition-colors"
            >
              {agent.owner.display_name ?? agent.owner.email}
            </Link>
            {agent.owner.display_name && (
              <span className="text-muted-foreground/70 truncate">{agent.owner.email}</span>
            )}
          </div>
        </div>
        <Button
          variant={agent.admin_paused ? "outline" : "destructive"}
          size="sm"
          disabled={pause.isPending}
          onClick={() => {
            setConfirming(true);
          }}
          className={cn(
            "shrink-0",
            agent.admin_paused &&
              "border-success/40 bg-success/10 text-success hover:bg-success/20 hover:text-success dark:bg-success/20 dark:hover:bg-success/30",
          )}
        >
          {agent.admin_paused ? <PlayIcon aria-hidden="true" /> : <PauseIcon aria-hidden="true" />}
          {agent.admin_paused ? t("admin.agents.actions.unpause") : t("admin.agents.actions.pause")}
        </Button>
      </header>

      <Section
        icon={SlidersHorizontalIcon}
        title={
          <>
            {t("admin.agents.card.configuration")}
            <Badge variant="secondary" className="font-normal">
              {t("admin.platform.readOnly")}
            </Badge>
          </>
        }
      >
        <div className="flex flex-col gap-4">
          {agent.description && (
            <Block label={t("agents.editor.description")}>
              <p className="text-sm">{agent.description}</p>
            </Block>
          )}
          <Block label={t("agents.editor.prompt")}>
            <p className="bg-muted/40 rounded-lg p-3 text-sm whitespace-pre-wrap">{agent.prompt}</p>
          </Block>
        </div>

        <dl className="border-border/60 mt-4 flex flex-col gap-2 border-t pt-4">
          <Fact icon={CpuIcon} label={t("agents.editor.model")}>
            {agent.model_name === null ? (
              <span className="text-warning">{t("agents.editor.modelNone")}</span>
            ) : (
              agent.model_name
            )}
          </Fact>
          <Fact icon={CalendarClockIcon} label={t("agents.schedule.label")}>
            {scheduleLabel(agent.schedule, t, i18n.language)}
          </Fact>
          <Fact icon={WrenchIcon} label={t("admin.agents.card.tools")}>
            <div className="flex flex-wrap items-center gap-1.5">
              {(optionsQuery.data?.core_tools ?? []).map((name) => (
                <Badge key={name} variant="secondary" className="gap-1">
                  <LockIcon className="size-3 opacity-60" aria-hidden="true" />
                  {name}
                </Badge>
              ))}
              {agent.tools.length === 0 ? (
                <span className="text-muted-foreground text-xs">
                  {t("admin.agents.card.noExternalTools")}
                </span>
              ) : (
                agent.tools.map((tool) => (
                  <Badge key={tool.id} variant="outline">
                    {tool.name}
                  </Badge>
                ))
              )}
            </div>
          </Fact>
        </dl>
      </Section>

      <Section icon={GaugeIcon} title={t("admin.agents.card.ownerBudget")}>
        <div className="flex flex-col gap-2">
          <div className="flex items-baseline justify-between gap-3">
            <span className="text-lg font-semibold tabular-nums">
              {formatTokens(budget.used, i18n.language)}
              <span className="text-muted-foreground font-normal">
                {" / "}
                {budget.limit === null
                  ? t("agents.stats.noLimit")
                  : formatTokens(budget.limit, i18n.language)}
              </span>
            </span>
            <span className="text-muted-foreground text-xs">
              {t("admin.agents.card.ownerBudgetHint")}
            </span>
          </div>
          {spendPct !== undefined && (
            <Progress
              value={Math.min(spendPct, 100)}
              className={cn(
                spendPct >= 100
                  ? "[&_[data-slot=progress-indicator]]:bg-destructive"
                  : spendPct >= 80 && "[&_[data-slot=progress-indicator]]:bg-warning",
              )}
            />
          )}
        </div>
      </Section>

      <section className="flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <HistoryIcon className="text-muted-foreground size-4" aria-hidden="true" />
          <h2 className="text-sm font-semibold">{t("admin.agents.card.journal")}</h2>
        </div>
        <RunJournal
          queryKey={agentsQueryKeys.adminRuns(id)}
          fetchPage={(cursor) => adminListRuns(id, cursor)}
        />
      </section>

      <PauseConfirmDialog
        paused={agent.admin_paused}
        open={confirming}
        onOpenChange={setConfirming}
        onConfirm={() => {
          pause.mutate(!agent.admin_paused);
          setConfirming(false);
        }}
      />
    </div>
  );
}

/** A titled card: icon + label over a hairline, matching the user card. */
function Section({
  icon: Icon,
  title,
  children,
}: {
  icon: LucideIcon;
  title: ReactNode;
  children: ReactNode;
}) {
  return (
    <Card className="shadow-2xs">
      <CardHeader className="items-center border-b">
        <CardTitle className="flex items-center gap-2 text-sm font-semibold">
          <Icon className="text-muted-foreground size-4" aria-hidden="true" />
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

/** One profile fact: muted icon + label, then value/control. */
function Fact({
  icon: Icon,
  label,
  children,
}: {
  icon: LucideIcon;
  label: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-start gap-x-3 gap-y-1">
      <dt className="text-muted-foreground flex w-28 shrink-0 items-center gap-1.5 pt-0.5 text-xs">
        <Icon className="size-3.5 shrink-0" aria-hidden="true" />
        {label}
      </dt>
      <dd className="min-w-0 flex-1 text-sm">{children}</dd>
    </div>
  );
}

/** A full-width labeled block for long values (description, prompt). */
function Block({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-muted-foreground text-xs">{label}</span>
      {children}
    </div>
  );
}
