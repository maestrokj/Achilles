import { useQuery } from "@tanstack/react-query";
import {
  ActivityIcon,
  ArchiveIcon,
  ArrowUpRightIcon,
  BotIcon,
  ChartColumnIcon,
  CheckIcon,
  ClockIcon,
  DatabaseIcon,
  LayersIcon,
  MailIcon,
  MessageCircleIcon,
  MessagesSquareIcon,
  PlusIcon,
  RefreshCwIcon,
  TriangleAlertIcon,
  UsersIcon,
  WaypointsIcon,
  type LucideIcon,
} from "lucide-react";
import { useContext, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router-dom";

import { api } from "@/api/client";
import { LIVE_STALE_TIME } from "@/api/freshness";
import { ComingSoonCard } from "@/components/ComingSoonCard";
import { TruncatedText } from "@/components/TruncatedText";
import { EmptyState } from "@/components/list-controls/EmptyState";
import { PageError } from "@/components/PageError";
import { StatusLine, type StatusTone } from "@/components/StatusLine";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { SessionContext } from "@/features/auth/session-context";
import { auditActionLabel, runStateLabel, runStateTone } from "@/lib/badges";
import { formatMoney, formatTokens, formatWhen } from "@/lib/format";
import { cn } from "@/lib/utils";

import type { AttentionItem, Dashboard } from "./types";

/** How many attention rows the dashboard shows before deferring to the feed. */
const ATTENTION_LIMIT = 5;

/** localStorage prefix (suffixed with the user id) remembering that this admin
 * dismissed the first-run setup checklist in this browser. */
const SETUP_DISMISSED_KEY = "achilles.admin.setup-dismissed";

interface SetupStep {
  key: "chatModels" | "embedding" | "sources" | "agentModels" | "email" | "surfaces";
  icon: LucideIcon;
  /** The screen (and section) where the step is completed. */
  to: string;
  done: boolean;
}

/** The first-run essentials in the order they unlock value: answers → knowledge
 * → agents → reach. Facts come from the dashboard aggregate; the sources step
 * derives from the tile the page already shows. */
function setupSteps(data: Dashboard): SetupStep[] {
  return [
    {
      key: "chatModels",
      icon: MessageCircleIcon,
      to: "/admin/ai-models#assignments",
      done: data.setup.chat_models,
    },
    {
      key: "embedding",
      icon: LayersIcon,
      to: "/admin/ai-models#assignments",
      done: data.setup.embedding,
    },
    { key: "sources", icon: DatabaseIcon, to: "/admin/harvester", done: data.sources.total > 0 },
    {
      key: "agentModels",
      icon: BotIcon,
      to: "/admin/ai-models#assignments",
      done: data.setup.agent_models,
    },
    { key: "email", icon: MailIcon, to: "/admin/platform#smtp", done: data.setup.email },
    {
      key: "surfaces",
      icon: MessagesSquareIcon,
      to: "/admin/platform#surfaces",
      done: data.setup.surfaces,
    },
  ];
}

function getDashboard(): Promise<Dashboard> {
  return api.get("admin/dashboard").json<Dashboard>();
}

/** Serif greeting keyed by the local hour — the dashboard's warm front door. */
function greetingKey(hour: number): "morning" | "day" | "evening" | "night" {
  if (hour >= 5 && hour < 12) return "morning";
  if (hour >= 12 && hour < 17) return "day";
  if (hour >= 17 && hour < 23) return "evening";
  return "night";
}

/** Admin · overview: tiles link to the sections that own the facts; the page
 * computes nothing. Wireframe: admin-panel/_wireframes/dashboard.html. */
export function DashboardPage() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const session = useContext(SessionContext);
  const dashboard = useQuery({
    queryKey: ["admin", "dashboard"],
    queryFn: getDashboard,
    staleTime: LIVE_STALE_TIME,
  });
  const [setupDismissed, setSetupDismissed] = useState(false);

  const firstName =
    session?.status === "authenticated"
      ? (session.user.full_name.trim().split(/\s+/)[0] ?? "")
      : "";
  const greeting = t(`admin.dashboard.hero.${greetingKey(new Date().getHours())}`, {
    name: firstName,
  });

  if (dashboard.isPending)
    return (
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
        <Header greeting={greeting} subtitle={t("admin.dashboard.title")} />
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-28 w-full rounded-xl" />
          ))}
        </div>
        <div className="grid gap-3 md:grid-cols-3">
          <Skeleton className="h-24 w-full rounded-xl" />
          <Skeleton className="h-24 w-full rounded-xl" />
          <Skeleton className="h-24 w-full rounded-xl" />
        </div>
      </div>
    );
  if (dashboard.isError)
    return <PageError className="max-w-6xl" onRetry={() => void dashboard.refetch()} />;
  const data = dashboard.data;

  const spendPct =
    data.spend.month_cost !== null && data.spend.budget !== null && Number(data.spend.budget) > 0
      ? (Number(data.spend.month_cost) / Number(data.spend.budget)) * 100
      : undefined;

  const steps = setupSteps(data);
  const dismissKey = `${SETUP_DISMISSED_KEY}.${String(
    session?.status === "authenticated" ? session.user.id : "",
  )}`;
  const showSetup =
    steps.some((step) => !step.done) &&
    !setupDismissed &&
    window.localStorage.getItem(dismissKey) === null;

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
      <Header greeting={greeting} subtitle={`${data.org_name} · ${data.timezone}`} />

      {showSetup && (
        <SetupCard
          steps={steps}
          onDismiss={() => {
            window.localStorage.setItem(dismissKey, "1");
            setSetupDismissed(true);
          }}
        />
      )}

      <div className="animate-in fade-in slide-in-from-bottom-2 grid grid-cols-2 gap-3 duration-500 md:grid-cols-3 xl:grid-cols-5">
        <Tile
          to="/admin/users"
          icon={UsersIcon}
          value={String(data.users.total)}
          label={t("admin.dashboard.users")}
          sub={t("admin.dashboard.usersSub", {
            invites: data.users.pending_invites,
            deactivated: data.users.deactivated,
          })}
        />
        <Tile
          to="/admin/harvester"
          icon={DatabaseIcon}
          value={String(data.sources.total)}
          label={t("admin.dashboard.sources")}
          sub={t("admin.dashboard.sourcesSub", {
            active: data.sources.active,
            paused: data.sources.paused,
            disconnected: data.sources.disconnected,
            failing: data.sources.failing,
          })}
        />
        <Tile
          to="/admin/knowledge-store"
          icon={WaypointsIcon}
          value={formatTokens(data.knowledge.entities, i18n.language)}
          label={t("admin.dashboard.entities")}
          sub={t("admin.dashboard.entitiesSub", {
            chunks: formatTokens(data.knowledge.chunks, i18n.language),
            edges: formatTokens(data.knowledge.edges, i18n.language),
          })}
        />
        <Tile
          to="/admin/agents"
          icon={BotIcon}
          value={String(data.agents.total)}
          label={t("admin.dashboard.agents")}
          sub={t("admin.dashboard.agentsSub", {
            active: data.agents.active,
            paused: data.agents.paused,
            failing: data.agents.failing,
          })}
        />
        <Tile
          to="/admin/ai-usage"
          icon={ChartColumnIcon}
          value={
            data.spend.month_cost === null ? "—" : formatMoney(data.spend.month_cost, i18n.language)
          }
          label={t("admin.dashboard.spend")}
          sub={
            data.spend.budget === null
              ? t("admin.dashboard.noBudget")
              : t("admin.dashboard.spendSub", {
                  budget: formatMoney(data.spend.budget, i18n.language),
                })
          }
          gauge={spendPct}
        />
      </div>

      {data.is_empty
        ? // The setup checklist already carries the connect-sources call.
          !showSetup && (
            <Card>
              <CardContent>
                <EmptyState
                  icon={DatabaseIcon}
                  description={t("admin.dashboard.emptyState")}
                  action={{
                    label: t("admin.dashboard.connectSources"),
                    icon: PlusIcon,
                    onClick: () => void navigate("/admin/harvester"),
                  }}
                />
              </CardContent>
            </Card>
          )
        : data.attention.length > 0 && <AttentionCard items={data.attention} />}

      <div className="grid gap-3 md:grid-cols-3">
        <StatusCard
          title={t("admin.dashboard.syncTitle")}
          icon={DatabaseIcon}
          to="/admin/harvester"
          linkLabel={t("admin.nav.harvester")}
        >
          {data.last_sync ? (
            data.last_sync.running > 0 ? (
              <StatusLine
                tone="primary"
                icon={RefreshCwIcon}
                spinning
                primary={t("admin.dashboard.syncRunning")}
              />
            ) : (
              <StatusLine
                {...syncVisual(data.last_sync.state)}
                primary={runStateLabel(data.last_sync.state, t)}
              />
            )
          ) : (
            <StatusLine tone="muted" icon={ClockIcon} primary={t("admin.dashboard.neverSynced")} />
          )}
        </StatusCard>

        <StatusCard
          title={t("admin.dashboard.curationTitle")}
          icon={WaypointsIcon}
          to="/admin/knowledge-store"
          linkLabel={t("admin.nav.knowledgeStore")}
        >
          {data.curation ? (
            <StatusLine
              tone="primary"
              icon={CheckIcon}
              spinning
              primary={runStateLabel(data.curation.state, t)}
            >
              {data.curation.reembed_total !== null &&
                data.curation.reembed_done !== null &&
                data.curation.reembed_total > 0 && (
                  <Progress
                    value={(data.curation.reembed_done / data.curation.reembed_total) * 100}
                  />
                )}
            </StatusLine>
          ) : (
            <StatusLine tone="muted" icon={CheckIcon} primary={t("admin.dashboard.curationIdle")} />
          )}
        </StatusCard>

        <StatusCard
          title={t("admin.dashboard.lastBackup")}
          icon={ArchiveIcon}
          to="/admin/knowledge-store"
          linkLabel={t("admin.nav.knowledgeStore")}
        >
          {data.last_backup ? (
            <StatusLine
              {...backupVisual(data.last_backup.state)}
              primary={runStateLabel(data.last_backup.state, t)}
            />
          ) : (
            <StatusLine tone="muted" icon={ClockIcon} primary={t("admin.dashboard.noBackups")} />
          )}
        </StatusCard>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <ComingSoonCard
          icon={ActivityIcon}
          title={t("admin.dashboard.systemHealth")}
          note={t("admin.dashboard.healthSoon")}
        />

        <Card>
          <CardContent className="flex flex-col gap-3">
            <SectionTitle>{t("admin.dashboard.quickActions")}</SectionTitle>
            <div className="flex flex-col gap-2">
              <Button size="lg" className="h-9 justify-start" render={<Link to="/admin/users" />}>
                <PlusIcon data-icon="inline-start" />
                {t("admin.dashboard.inviteUser")}
              </Button>
              <Button
                variant="outline"
                size="lg"
                className="h-9 justify-start"
                render={<Link to="/admin/harvester" />}
              >
                <PlusIcon data-icon="inline-start" />
                {t("admin.harvester.addSource")}
              </Button>
            </div>
            {(data.tasks.pending_invites > 0 || data.tasks.unmatched_identities > 0) && (
              <div className="border-border/60 text-muted-foreground flex flex-col gap-1.5 border-t pt-3 text-xs">
                {data.tasks.pending_invites > 0 && (
                  <Link
                    to="/admin/users"
                    className="hover:text-foreground flex items-center gap-1.5 transition-colors"
                  >
                    <ClockIcon className="size-3.5" />
                    {t("admin.dashboard.pendingInvites", { count: data.tasks.pending_invites })}
                  </Link>
                )}
                {data.tasks.unmatched_identities > 0 && (
                  <Link
                    to="/admin/users"
                    className="hover:text-foreground flex items-center gap-1.5 transition-colors"
                  >
                    <ClockIcon className="size-3.5" />
                    {t("admin.dashboard.unmatchedIdentities", {
                      count: data.tasks.unmatched_identities,
                    })}
                  </Link>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {data.audit && <RecentActivityCard rows={data.audit} />}
    </div>
  );
}

/** First-run checklist — the concierge card that walks an admin through the
 * essentials. It shows while a step is pending and the viewer hasn't dismissed
 * it; completing every step retires it on its own. */
function SetupCard({ steps, onDismiss }: { steps: SetupStep[]; onDismiss: () => void }) {
  const { t } = useTranslation();
  const done = steps.filter((step) => step.done).length;
  return (
    // A bolder neutral frame lifts the card above the grid without loud
    // color — it must read first on a fresh install.
    <Card className="animate-in fade-in slide-in-from-bottom-2 ring-foreground/25 relative gap-0 overflow-hidden py-0 duration-500">
      <div
        className="bg-primary/5 pointer-events-none absolute -top-20 -right-16 size-64 rounded-full blur-3xl"
        aria-hidden
      />
      <div className="border-border/70 flex items-center gap-4 border-b px-5 py-4">
        <ProgressRing done={done} total={steps.length} />
        <div className="flex min-w-0 flex-col gap-0.5">
          <h2 className="font-serif text-xl tracking-tight text-balance">
            {t("admin.dashboard.setup.title")}
          </h2>
          <p className="text-muted-foreground text-sm">{t("admin.dashboard.setup.subtitle")}</p>
        </div>
      </div>
      <div className="grid sm:grid-cols-2">
        {steps.map((step) => (
          <SetupStepRow key={step.key} step={step} />
        ))}
      </div>
      <div className="border-border/70 text-muted-foreground flex flex-wrap items-center justify-between gap-x-4 gap-y-1 border-t px-5 py-2.5 text-xs">
        <span>{t("admin.dashboard.setup.autoHide")}</span>
        <button
          type="button"
          onClick={onDismiss}
          className="hover:text-foreground shrink-0 cursor-pointer font-medium transition-colors"
        >
          {t("admin.dashboard.setup.dismiss")}
        </button>
      </div>
    </Card>
  );
}

/** One checklist step: a status disc (step icon → success check), the title and
 * a one-line hint, linking to the screen where the step is completed. */
function SetupStepRow({ step }: { step: SetupStep }) {
  const { t } = useTranslation();
  const Icon = step.icon;
  return (
    <Link
      to={step.to}
      className={cn(
        "group/step border-border/60 flex items-center gap-3 border-b px-5 py-3.5 transition-colors last:border-b-0",
        "sm:odd:border-r sm:[&:nth-last-child(-n+2)]:border-b-0",
        step.done ? "hover:bg-muted/30" : "hover:bg-muted/50",
      )}
    >
      <span
        className={cn(
          "flex size-8 shrink-0 items-center justify-center rounded-full border transition-colors",
          step.done
            ? "border-success/30 bg-success/10 text-success"
            : "border-border bg-background text-muted-foreground group-hover/step:border-primary/40 group-hover/step:text-primary",
        )}
      >
        {step.done ? <CheckIcon className="size-4" /> : <Icon className="size-4" />}
      </span>
      <span className="flex min-w-0 flex-1 flex-col">
        <span className={cn("truncate text-sm font-medium", step.done && "text-muted-foreground")}>
          {t(`admin.dashboard.setup.steps.${step.key}.title`)}
        </span>
        <span className="text-muted-foreground/80 truncate text-xs">
          {step.done
            ? t("admin.dashboard.setup.stepDone")
            : t(`admin.dashboard.setup.steps.${step.key}.hint`)}
        </span>
      </span>
      {!step.done && (
        <ArrowUpRightIcon className="text-muted-foreground/40 size-4 shrink-0 opacity-0 transition-opacity group-hover/step:opacity-100" />
      )}
    </Link>
  );
}

/** A small completion dial: the done/total arc with the count in the middle. */
function ProgressRing({ done, total }: { done: number; total: number }) {
  const radius = 20;
  const circumference = 2 * Math.PI * radius;
  return (
    <div className="relative size-12 shrink-0">
      <svg viewBox="0 0 48 48" className="size-12 -rotate-90" aria-hidden>
        <circle
          cx="24"
          cy="24"
          r={radius}
          fill="none"
          strokeWidth={3.5}
          className="stroke-border/70"
        />
        <circle
          cx="24"
          cy="24"
          r={radius}
          fill="none"
          strokeWidth={3.5}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - (total > 0 ? done / total : 0))}
          className="stroke-primary transition-[stroke-dashoffset] duration-700 ease-out"
        />
      </svg>
      <span className="absolute inset-0 flex items-center justify-center text-[11px] font-semibold tabular-nums">
        {done}/{total}
      </span>
    </div>
  );
}

/** Serif greeting + muted subtitle — the personal header of the overview. */
function Header({ greeting, subtitle }: { greeting: string; subtitle: string }) {
  return (
    <div className="animate-in fade-in slide-in-from-bottom-1 flex flex-col gap-1 duration-500">
      <h1 className="font-serif text-3xl tracking-tight text-balance">{greeting}</h1>
      <p className="text-muted-foreground text-sm">{subtitle}</p>
    </div>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <h2 className="text-sm font-semibold">{children}</h2>;
}

function Tile({
  to,
  icon: Icon,
  value,
  label,
  sub,
  gauge,
}: {
  to: string;
  icon: LucideIcon;
  value: string;
  label: string;
  sub: string;
  /** Percent of the budget threshold used — draws a soft gauge under the value. */
  gauge?: number;
}) {
  return (
    <Link to={to} className="group/tile">
      <Card className="hover:border-primary/40 hover:ring-primary/20 h-full gap-0 transition-all group-hover/tile:shadow-sm">
        <CardContent className="flex h-full flex-col gap-2">
          <div className="flex items-center justify-between">
            <div className="bg-secondary text-muted-foreground flex size-8 items-center justify-center rounded-lg">
              <Icon className="size-4" />
            </div>
            <ArrowUpRightIcon className="text-muted-foreground/40 size-4 opacity-0 transition-opacity group-hover/tile:opacity-100" />
          </div>
          <span className="mt-1 text-2xl font-semibold tracking-tight tabular-nums">{value}</span>
          {gauge !== undefined && (
            <Progress
              value={Math.min(gauge, 100)}
              className={cn(
                gauge >= 100
                  ? "[&_[data-slot=progress-indicator]]:bg-destructive"
                  : gauge >= 80 && "[&_[data-slot=progress-indicator]]:bg-warning",
              )}
            />
          )}
          <div className="mt-auto flex min-w-0 flex-col gap-0.5">
            <span className="truncate text-xs font-medium">{label}</span>
            <span className="text-muted-foreground truncate text-xs" title={sub}>
              {sub}
            </span>
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}

/** A compact status card (sync / curation / backup) with a title and a corner
 * deep-link to the section that owns the fact. */
function StatusCard({
  title,
  icon: Icon,
  to,
  linkLabel,
  children,
}: {
  title: string;
  icon: LucideIcon;
  to: string;
  linkLabel: string;
  children: React.ReactNode;
}) {
  return (
    <Link to={to} aria-label={linkLabel} className="group/card">
      <Card className="hover:border-primary/40 hover:ring-primary/20 h-full transition-all group-hover/card:shadow-sm">
        <CardContent className="flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Icon className="text-muted-foreground size-4" />
              <SectionTitle>{title}</SectionTitle>
            </div>
            <ArrowUpRightIcon className="text-muted-foreground/40 size-4 opacity-0 transition-opacity group-hover/card:opacity-100" />
          </div>
          {children}
        </CardContent>
      </Card>
    </Link>
  );
}

/** Tone + icon for the freshest sync run's state (running is handled upstream). */
function syncVisual(state: string): { tone: StatusTone; icon: LucideIcon; spinning?: boolean } {
  const tone = runStateTone(state);
  if (state === "succeeded") return { tone, icon: CheckIcon };
  if (state === "failed") return { tone, icon: TriangleAlertIcon };
  if (state === "cancelled") return { tone, icon: ClockIcon };
  return { tone, icon: RefreshCwIcon, spinning: true };
}

/** Tone + icon for a backup snapshot state (running is the only spinning case). */
function backupVisual(state: string): { tone: StatusTone; icon: LucideIcon; spinning?: boolean } {
  const tone = runStateTone(state);
  if (state === "succeeded") return { tone, icon: CheckIcon };
  if (state === "failed") return { tone, icon: TriangleAlertIcon };
  return { tone, icon: CheckIcon, spinning: true };
}

/** Recent activity — the audit tail, dressed like the attention card: a bordered
 * header, divided rows, and a footer that opens the full audit journal. The
 * server caps the tail (dashboard.py AUDIT_TOP); the footer is the way to more. */
function RecentActivityCard({ rows }: { rows: NonNullable<Dashboard["audit"]> }) {
  const { t, i18n } = useTranslation();
  return (
    <Card className="gap-0 py-0">
      <div className="border-border/70 flex items-center border-b px-4 py-3">
        <SectionTitle>{t("admin.dashboard.recentActivity")}</SectionTitle>
      </div>
      <div className="flex flex-col">
        {rows.map((row, index) => (
          <div
            key={index}
            className="border-border/60 flex items-center gap-2.5 border-b px-4 py-3 text-sm last:border-0"
          >
            <span className="text-muted-foreground shrink-0 text-xs whitespace-nowrap tabular-nums">
              {formatWhen(row.created_at, i18n.language)}
            </span>
            <span className="min-w-0 flex-1 truncate">{auditActionLabel(row.action, t, i18n)}</span>
            {row.actor_email && (
              <TruncatedText className="text-muted-foreground hidden max-w-[12rem] text-xs sm:block">
                {row.actor_email}
              </TruncatedText>
            )}
            <span
              className={cn(
                "size-1.5 shrink-0 rounded-full",
                row.success ? "bg-success" : "bg-destructive",
              )}
              title={row.success ? t("admin.dashboard.auditOk") : t("admin.dashboard.auditFail")}
            />
          </div>
        ))}
      </div>
      <Link
        to="/admin/audit-log"
        className="text-muted-foreground hover:text-foreground hover:bg-muted/40 flex items-center justify-center gap-1.5 border-t px-4 py-2.5 text-xs font-medium transition-colors"
      >
        {t("admin.nav.auditLog")}
        <ArrowUpRightIcon className="size-3.5" />
      </Link>
    </Card>
  );
}

function AttentionCard({ items }: { items: AttentionItem[] }) {
  const { t } = useTranslation();
  const criticalCount = items.filter((item) => item.severity === "critical").length;
  const shown = items.slice(0, ATTENTION_LIMIT);
  const overflow = items.length - shown.length;
  return (
    <Card className="animate-in fade-in slide-in-from-bottom-2 gap-0 py-0 duration-500">
      <div className="border-border/70 flex items-center justify-between gap-2 border-b px-4 py-3">
        <SectionTitle>{t("admin.dashboard.attention")}</SectionTitle>
        {criticalCount > 0 && (
          <div className="flex items-center gap-3">
            <Badge variant="destructive">
              {t("admin.dashboard.criticalCount", { count: criticalCount })}
            </Badge>
            <ArrowUpRightIcon className="size-4 shrink-0 opacity-0" aria-hidden />
          </div>
        )}
      </div>
      <div className="flex flex-col">
        {shown.map((item, index) => (
          <AttentionRow key={`${item.kind}-${String(index)}`} item={item} />
        ))}
      </div>
      <Link
        to="/admin/notifications/inbox"
        className="text-muted-foreground hover:text-foreground hover:bg-muted/40 flex items-center justify-center gap-1.5 border-t px-4 py-2.5 text-xs font-medium transition-colors"
      >
        {overflow > 0 ? t("admin.dashboard.moreSignals", { count: overflow }) : null}
        {overflow > 0 && <span className="text-muted-foreground/50">·</span>}
        {t("admin.dashboard.fullFeed")}
        <ArrowUpRightIcon className="size-3.5" />
      </Link>
    </Card>
  );
}

/** The route that fixes the signal — the frontend owns the kind → screen map. */
function attentionRoute(item: AttentionItem): string {
  switch (item.kind) {
    case "source_failing":
    case "dlq":
      return item.source_id === null
        ? "/admin/harvester"
        : `/admin/harvester/sources/${String(item.source_id)}`;
    case "backup_failed":
      return "/admin/knowledge-store#backups";
    case "provider_error":
      return "/admin/ai-models#ai-providers";
    case "budget":
      return "/admin/ai-usage#limits";
  }
}

function AttentionRow({ item }: { item: AttentionItem }) {
  const { t } = useTranslation();
  const critical = item.severity === "critical";
  return (
    <Link
      to={attentionRoute(item)}
      className={cn(
        "border-border/60 group/row flex items-center gap-3 border-b px-4 py-3 text-sm transition-colors last:border-0",
        critical ? "hover:bg-destructive/5" : "hover:bg-muted/50",
      )}
    >
      <span
        className={cn("size-2 shrink-0 rounded-full", critical ? "bg-destructive" : "bg-warning")}
      />
      <span className="min-w-0 flex-1">
        {t(`admin.dashboard.signals.${item.kind}`, {
          subject: item.subject ?? "",
          count: item.count ?? 0,
        })}
      </span>
      <Badge variant={critical ? "destructive" : "outline"}>
        {t(`notifications.severities.${item.severity}`)}
      </Badge>
      <ArrowUpRightIcon className="text-muted-foreground/40 size-4 shrink-0 opacity-0 transition-opacity group-hover/row:opacity-100" />
    </Link>
  );
}
