import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { BotIcon, MessagesSquareIcon } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";

import { LIVE_STALE_TIME } from "@/api/freshness";
import { BackLink } from "@/components/BackLink";
import { TruncatedText } from "@/components/TruncatedText";
import {
  DataTable,
  SortableHead,
  TableFrame,
  TruncateCell,
} from "@/components/list-controls/DataTable";
import { EmptyState } from "@/components/list-controls/EmptyState";
import { useClientSort, type SortAccessors } from "@/components/list-controls/useClientSort";
import { StatTile } from "@/components/StatTile";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { PageSkeleton } from "@/components/PageSkeleton";
import { TableBody, TableCell, TableHeader, TableRow } from "@/components/ui/table";
import { formatTokens, initials } from "@/lib/format";
import { cn } from "@/lib/utils";

import { aiKeys, getUserUsage } from "./api";
import type { UserUsage } from "./types";

const WINDOWS = ["week", "prev_week", "month"] as const;

type AgentRow = UserUsage["agents"][number];
type ChatRow = UserUsage["chat"][number];

type AgentSortKey = "agent" | "model" | "runs" | "tokens";
const AGENT_SORT: SortAccessors<AgentRow, AgentSortKey> = {
  agent: (row) => row.name.toLowerCase(),
  model: (row) => (row.model ?? "").toLowerCase(),
  runs: (row) => row.runs,
  tokens: (row) => row.tokens,
};

type ChatSortKey = "model" | "messages" | "tokens";
const CHAT_SORT: SortAccessors<ChatRow, ChatSortKey> = {
  model: (row) => row.model.toLowerCase(),
  messages: (row) => row.messages,
  tokens: (row) => row.tokens,
};

/** Admin · one person's AI spend: agents by agent, chat by model, each against
 * its weekly ceiling. Wireframe: admin-panel/_wireframes/usage-detail.html. */
export function UsageDetailPage() {
  const { t, i18n } = useTranslation();
  const { userId = "" } = useParams();
  const id = Number(userId);
  const [window, setWindow] = useState<(typeof WINDOWS)[number]>("week");
  const usage = useQuery({
    queryKey: aiKeys.userUsage(id, window),
    queryFn: () => getUserUsage(id, window),
    placeholderData: keepPreviousData,
    staleTime: LIVE_STALE_TIME,
  });

  if (usage.isPending) return <PageSkeleton />;
  if (usage.isError)
    return (
      <EmptyState
        variant="error"
        onRetry={() => {
          void usage.refetch();
        }}
      />
    );
  const data = usage.data;

  const tokens = (value: number) => formatTokens(value, i18n.language);
  const agentLimit = data.limits.agent_weekly_token_budget;
  const chatLimit = data.limits.chat_weekly_token_budget;

  const agentTotal = data.agent_tokens;
  const chatTotal = data.chat_tokens;

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
      <div className="animate-in fade-in slide-in-from-bottom-1 flex flex-col gap-4 duration-500">
        <BackLink to="/admin/ai-usage" label={t("admin.nav.aiUsage")} />
        <div className="flex items-start justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3.5">
            <div
              aria-hidden="true"
              className="bg-muted grid size-11 shrink-0 place-items-center rounded-2xl text-sm font-semibold tracking-wide"
            >
              {initials(data.full_name)}
            </div>
            <div className="flex min-w-0 flex-col gap-0.5">
              <h1 className="min-w-0 text-2xl font-semibold tracking-tight">
                <TruncatedText>{data.full_name}</TruncatedText>
              </h1>
              <TruncatedText className="text-muted-foreground text-sm">{data.email}</TruncatedText>
            </div>
          </div>
          <Select
            items={WINDOWS.map((value) => ({
              value,
              label: t(`admin.usage.windows.${value}`),
            }))}
            value={window}
            onValueChange={(value) => {
              if (value) setWindow(value);
            }}
          >
            <SelectTrigger size="sm" className="w-40 shrink-0">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {WINDOWS.map((value) => (
                <SelectItem key={value} value={value}>
                  {t(`admin.usage.windows.${value}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      <div className="animate-in fade-in slide-in-from-bottom-2 grid grid-cols-1 gap-4 duration-500 sm:grid-cols-3">
        <Meter
          variant="blocking"
          label={t("admin.usage.columns.agents")}
          used={agentTotal}
          cap={agentLimit}
          tokens={tokens}
        />
        <Meter
          variant="advisory"
          label={t("admin.usage.columns.chat")}
          used={chatTotal}
          cap={chatLimit}
          tokens={tokens}
        />
        <StatTile value={tokens(agentTotal + chatTotal)} label={t("admin.usage.columns.total")} />
      </div>

      <SectionCard
        icon={BotIcon}
        title={t("admin.usage.columns.agents")}
        className="animate-in fade-in slide-in-from-bottom-2 duration-500"
      >
        <AgentsTable rows={data.agents} tokens={tokens} />
      </SectionCard>

      <SectionCard
        icon={MessagesSquareIcon}
        title={t("admin.usage.columns.chat")}
        className="animate-in fade-in slide-in-from-bottom-2 duration-500"
      >
        <ChatTable rows={data.chat} tokens={tokens} />
      </SectionCard>
    </div>
  );
}

/** Per-agent spend, sorted client-side — heaviest first by default, any column
 * on demand. */
function AgentsTable({ rows, tokens }: { rows: AgentRow[]; tokens: (value: number) => string }) {
  const { t } = useTranslation();
  const {
    sorted,
    sort,
    toggle: toggleSort,
  } = useClientSort(rows, AGENT_SORT, { key: "tokens", desc: true });
  return (
    <TableFrame variant="card">
      <DataTable>
        <TableHeader>
          <TableRow>
            <SortableHead
              label={t("admin.usage.detail.agent")}
              sortKey="agent"
              sort={sort}
              onToggle={toggleSort}
            />
            <SortableHead
              label={t("admin.usage.columns.model")}
              sortKey="model"
              sort={sort}
              onToggle={toggleSort}
            />
            <SortableHead
              label={t("admin.usage.detail.runs")}
              sortKey="runs"
              sort={sort}
              onToggle={toggleSort}
              align="center"
            />
            <SortableHead
              label={t("admin.usage.detail.tokens")}
              sortKey="tokens"
              sort={sort}
              onToggle={toggleSort}
              align="center"
            />
          </TableRow>
        </TableHeader>
        <TableBody>
          {sorted.length === 0 ? (
            <EmptyRow span={4} label={t("admin.usage.detail.noActivity")} />
          ) : (
            sorted.map((row) => (
              <TableRow key={row.agent_id} className="hover:bg-muted/40 h-12">
                <TruncateCell className="max-w-[16rem]" text={row.name}>
                  <Link
                    to={`/admin/agents/${String(row.agent_id)}`}
                    className="font-medium hover:underline"
                  >
                    {row.name}
                  </Link>
                </TruncateCell>
                <TruncateCell
                  className="text-muted-foreground max-w-[16rem] text-sm"
                  text={row.model ?? "—"}
                />
                <TableCell className="text-center tabular-nums">{row.runs}</TableCell>
                <TableCell className="text-center tabular-nums">{tokens(row.tokens)}</TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </DataTable>
    </TableFrame>
  );
}

/** Per-model chat spend, sorted client-side — heaviest first by default. */
function ChatTable({ rows, tokens }: { rows: ChatRow[]; tokens: (value: number) => string }) {
  const { t } = useTranslation();
  const {
    sorted,
    sort,
    toggle: toggleSort,
  } = useClientSort(rows, CHAT_SORT, { key: "tokens", desc: true });
  return (
    <TableFrame variant="card">
      <DataTable>
        <TableHeader>
          <TableRow>
            <SortableHead
              label={t("admin.usage.columns.model")}
              sortKey="model"
              sort={sort}
              onToggle={toggleSort}
            />
            <SortableHead
              label={t("admin.usage.detail.messages")}
              sortKey="messages"
              sort={sort}
              onToggle={toggleSort}
              align="center"
            />
            <SortableHead
              label={t("admin.usage.detail.tokens")}
              sortKey="tokens"
              sort={sort}
              onToggle={toggleSort}
              align="center"
            />
          </TableRow>
        </TableHeader>
        <TableBody>
          {sorted.length === 0 ? (
            <EmptyRow span={3} label={t("admin.usage.detail.noActivity")} />
          ) : (
            sorted.map((row) => (
              <TableRow key={row.model} className="hover:bg-muted/40 h-12">
                <TruncateCell className="max-w-[16rem] font-medium" text={row.model} />
                <TableCell className="text-center tabular-nums">{row.messages}</TableCell>
                <TableCell className="text-center tabular-nums">{tokens(row.tokens)}</TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </DataTable>
    </TableFrame>
  );
}

/** A budget tile: big token count against its weekly cap and a gauge. The gauge
 * and value take on tone once the cap is crossed — destructive for the blocking
 * agent cap, warning for the advisory chat cap. */
function Meter({
  variant,
  label,
  used,
  cap,
  tokens,
}: {
  variant: "blocking" | "advisory";
  label: string;
  used: number;
  cap: number | null;
  tokens: (value: number) => string;
}) {
  const { t } = useTranslation();
  const over = cap !== null && used >= cap;
  const percent = cap ? Math.round((used / cap) * 100) : null;

  const toneText = variant === "blocking" ? "text-destructive" : "text-warning";
  const toneBar =
    variant === "blocking"
      ? "[&_[data-slot=progress-indicator]]:bg-destructive"
      : "[&_[data-slot=progress-indicator]]:bg-warning";

  return (
    <Card className="shadow-2xs">
      <CardContent className="flex flex-col gap-1">
        <div className="flex items-center justify-between gap-2">
          <span className="text-muted-foreground text-xs">{label}</span>
          {over &&
            (variant === "blocking" ? (
              <Badge variant="destructive">{t("admin.usage.overLimit")}</Badge>
            ) : (
              <Badge variant="secondary" className="bg-warning/15 text-warning">
                {t("admin.usage.overSoftLimit")}
              </Badge>
            ))}
        </div>
        <span className={cn("text-2xl font-semibold tabular-nums", over && toneText)}>
          {tokens(used)}
          {cap !== null && (
            <span className="text-muted-foreground text-base font-normal"> / {tokens(cap)}</span>
          )}
        </span>
        {cap !== null && (
          <Progress
            value={percent === null ? 0 : Math.min(percent, 100)}
            className={cn("mt-1", over && toneBar)}
          />
        )}
      </CardContent>
    </Card>
  );
}

/** A titled card section: muted leading icon anchors the title, matching the
 * dashboard's section chrome. */
function SectionCard({
  icon: Icon,
  title,
  className,
  children,
}: {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <Card className={cn("shadow-2xs", className)}>
      <CardHeader>
        <div className="flex items-center gap-2">
          <Icon className="text-muted-foreground size-4" aria-hidden="true" />
          <CardTitle className="text-sm">{title}</CardTitle>
        </div>
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

/** Quiet placeholder row for a period with no rows to show. */
function EmptyRow({ span, label }: { span: number; label: string }) {
  return (
    <TableRow className="hover:bg-transparent">
      <TableCell colSpan={span} className="text-muted-foreground h-16 text-center text-sm">
        {label}
      </TableCell>
    </TableRow>
  );
}
