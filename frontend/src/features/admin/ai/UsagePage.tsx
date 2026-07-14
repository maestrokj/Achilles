import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronRightIcon,
  CpuIcon,
  SlidersHorizontalIcon,
  UsersIcon,
  type LucideIcon,
} from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { toast } from "@/lib/toast";

import type { TFunction } from "i18next";

import { toastApiError } from "@/api/errors";
import { LIVE_STALE_TIME } from "@/api/freshness";
import type { ListQuery } from "@/api/lists";
import { InfoHint } from "@/components/InfoHint";
import {
  DataTable,
  ROW_LINK_ABOVE,
  ROW_LINK_ROW,
  RowLink,
  SortableHead,
  TableFrame,
  TruncateCell,
} from "@/components/list-controls/DataTable";
import { EmptyState } from "@/components/list-controls/EmptyState";
import { FacetSelect } from "@/components/list-controls/FacetSelect";
import { Pagination } from "@/components/list-controls/Pagination";
import { SearchInput } from "@/components/list-controls/SearchInput";
import { useClientSort, type SortAccessors } from "@/components/list-controls/useClientSort";
import {
  buildListQuery,
  type PerPage,
  useListState,
} from "@/components/list-controls/useListState";
import { SelectField } from "@/components/SelectField";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  getPlatformSettings,
  patchPlatformSettings,
  platformKeys,
} from "@/features/admin/platform/api";
import { formatMoney, formatNumber, formatTokens } from "@/lib/format";
import { useHashTarget } from "@/lib/useHashTarget";
import { cn } from "@/lib/utils";

import { aiKeys, getUsage } from "./api";
import type { ModelSpend, Usage } from "./types";

const WINDOWS = ["week", "prev_week", "month"] as const;
const MODEL_DAYS = [7, 30, 90] as const;
/** Both spend tables open on a compact page — the screen is a leaderboard, not
 * a directory; a short first page reads faster. */
const SPEND_PER_PAGE: PerPage = 10;

type ModelSortKey = "model" | "function" | "requests" | "input" | "output" | "cost";
const MODEL_SORT: SortAccessors<ModelSpend, ModelSortKey> = {
  model: (row) => (row.display_name ?? "").toLowerCase(),
  function: (row) => row.function,
  requests: (row) => row.request_count,
  input: (row) => row.input_tokens,
  output: (row) => row.output_tokens,
  // Unpriced rows sort below any real cost.
  cost: (row) => (row.cost === null ? -1 : Number(row.cost)),
};

/** model_usage.function tokens → human labels; embedding splits into indexing
 * (harvester) vs online search (query_rag). Unknown tokens fall back to raw. */
const FUNCTION_KEYS = ["chat", "agent_engine", "harvester_embedding", "query_rag"] as const;
function functionLabel(t: TFunction, fn: string): string {
  return (FUNCTION_KEYS as readonly string[]).includes(fn)
    ? t(`admin.usage.functionLabels.${fn as (typeof FUNCTION_KEYS)[number]}`)
    : fn;
}

/** Admin · AI spend: company panorama, limits, per-person, per-model.
 * Wireframe: admin-panel/_wireframes/usage.html. */
export function UsagePage() {
  const { t, i18n } = useTranslation();
  useHashTarget();
  const list = useListState(["role"], SPEND_PER_PAGE);
  const [window, setWindow] = useState<(typeof WINDOWS)[number]>("week");
  const [modelDays, setModelDays] = useState<(typeof MODEL_DAYS)[number]>(30);
  const query: ListQuery = {
    ...buildListQuery(list, SPEND_PER_PAGE),
    // buildListQuery keeps the default page size out of the URL, but the server's
    // own default is 50 — without this the by-user table pages at 50 while the UI
    // is built for 10. The page size must always cross the wire.
    per_page: list.perPage,
    window,
    model_days: modelDays,
  };
  const usage = useQuery({
    queryKey: aiKeys.usage(query),
    queryFn: () => getUsage(query),
    placeholderData: keepPreviousData,
    staleTime: LIVE_STALE_TIME,
  });

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
      <h1 className="text-2xl font-semibold tracking-tight">{t("admin.nav.aiUsage")}</h1>

      {usage.isPending ? (
        <Skeleton className="h-64 w-full" />
      ) : usage.isError ? (
        <EmptyState
          variant="error"
          onRetry={() => {
            void usage.refetch();
          }}
        />
      ) : (
        <>
          <TotalsTiles usage={usage.data} />
          <LimitsCard />
          <Card className="shadow-2xs">
            <SectionHeader icon={UsersIcon} title={t("admin.usage.byUser")} />
            <CardContent className="flex flex-col gap-3">
              <div className="flex flex-wrap items-center gap-2">
                <SearchInput
                  value={list.input}
                  onChange={list.setInput}
                  onClear={list.clearSearch}
                  placeholder={t("admin.users.searchPlaceholder")}
                />
                <SelectField
                  size="sm"
                  className="w-40"
                  options={WINDOWS.map((value) => ({
                    value,
                    label: t(`admin.usage.windows.${value}`),
                  }))}
                  value={window}
                  onValueChange={setWindow}
                />
                <FacetSelect
                  label={t("admin.users.facets.role")}
                  options={["owner", "admin", "member"].map((value) => ({
                    value,
                    label: value,
                  }))}
                  selected={list.facets["role"] ?? []}
                  onToggle={(value) => {
                    list.toggleFacet("role", value);
                  }}
                />
              </div>
              <TableFrame variant="card">
                <DataTable>
                  <TableHeader>
                    <TableRow>
                      <TableHead>{t("admin.usage.columns.person")}</TableHead>
                      <TableHead>{t("admin.usage.columns.agents")}</TableHead>
                      <TableHead>{t("admin.usage.columns.chat")}</TableHead>
                      <TableHead>{t("admin.usage.columns.total")}</TableHead>
                      <TableHead />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {usage.data.by_user.items.map((row) => (
                      <TableRow key={row.user_id} className={`${ROW_LINK_ROW} h-12`}>
                        <TableCell className="max-w-[16rem]">
                          <RowLink to={`/admin/users/${String(row.user_id)}`}>
                            {row.full_name}
                          </RowLink>
                          <span
                            className="text-muted-foreground block truncate text-xs"
                            title={row.email}
                          >
                            {row.email}
                          </span>
                        </TableCell>
                        <TableCell className="tabular-nums">
                          {formatTokens(row.agent_tokens, i18n.language)}
                          {row.agent_over_limit && (
                            <Badge variant="destructive" className="ml-2">
                              {t("admin.usage.overLimit")}
                            </Badge>
                          )}
                        </TableCell>
                        <TableCell className="tabular-nums">
                          {formatTokens(row.chat_tokens, i18n.language)}
                          {row.chat_over_limit && (
                            <Badge variant="secondary" className="bg-warning/15 text-warning ml-2">
                              {t("admin.usage.overSoftLimit")}
                            </Badge>
                          )}
                        </TableCell>
                        <TableCell className="font-medium tabular-nums">
                          {formatTokens(row.total_tokens, i18n.language)}
                        </TableCell>
                        {/* The name links to the profile like everywhere else; the
                            row's spend breakdown gets its own explicit action. */}
                        <TableCell className={`${ROW_LINK_ABOVE} text-right`}>
                          <Link
                            to={`/admin/ai-usage/${String(row.user_id)}`}
                            className="text-muted-foreground hover:text-foreground inline-flex items-center gap-0.5 text-sm whitespace-nowrap"
                          >
                            {t("admin.usage.viewDetail")}
                            <ChevronRightIcon className="size-4" />
                          </Link>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </DataTable>
              </TableFrame>
              <Pagination
                page={usage.data.by_user.page}
                perPage={list.perPage}
                total={usage.data.by_user.total}
                onPageChange={list.setPage}
                onPerPageChange={list.setPerPage}
              />
            </CardContent>
          </Card>
          <ByModelCard usage={usage.data} days={modelDays} onDaysChange={setModelDays} />
        </>
      )}
    </div>
  );
}

/** Card section header: a muted leading icon anchors the title; an optional
 * trailing control (a filter) rides the right corner via CardHeader's grid
 * action slot. Mirrors the dashboard's section chrome. */
function SectionHeader({
  icon: Icon,
  title,
  children,
  divider,
}: {
  icon: LucideIcon;
  title: string;
  children?: React.ReactNode;
  divider?: boolean;
}) {
  return (
    <CardHeader className={divider ? "border-b" : undefined}>
      <div className="flex items-center gap-2">
        <Icon className="text-muted-foreground size-4" />
        <CardTitle className="text-sm">{title}</CardTitle>
      </div>
      {children && <CardAction className="self-center">{children}</CardAction>}
    </CardHeader>
  );
}

/** A panorama tile with money as the headline and tokens as its supporting line.
 * Spend is what the screen is about, so the dollar figure is the hero; the token
 * count rides beneath it, always carrying its unit word so the two never blur.
 * When a window has no pricing, tokens take the headline (there is no money to
 * lead with) and the unpriced note explains the gap. */
function SpendTile({
  label,
  tokens,
  cost,
  tone,
  progress,
  note,
}: {
  label: string;
  tokens: number;
  cost: string | null;
  /** "warning" colors the money value — a threshold has been crossed. */
  tone?: "warning";
  /** Percent of the budget alert already spent; renders a bar when set. */
  progress?: number;
  /** A trailing line under the tile — the month's alert-threshold caption. */
  note?: string;
}) {
  const { t, i18n } = useTranslation();
  const priced = cost !== null;
  return (
    <Card className="flex-1 shadow-2xs">
      <CardContent className="flex flex-col gap-1">
        <span className="text-muted-foreground text-xs">{label}</span>
        {priced ? (
          <>
            <span
              className={cn(
                "text-2xl font-semibold tabular-nums",
                tone === "warning" && "text-warning",
              )}
            >
              {formatMoney(cost, i18n.language)}
            </span>
            <span className="text-muted-foreground text-xs tabular-nums">
              {formatTokens(tokens, i18n.language)}{" "}
              <span className="font-normal">{t("admin.usage.tokensUnit")}</span>
            </span>
          </>
        ) : (
          <>
            <span className="text-2xl font-semibold tabular-nums">
              {formatTokens(tokens, i18n.language)}
              <span className="text-muted-foreground ml-1 text-sm font-normal">
                {t("admin.usage.tokensUnit")}
              </span>
            </span>
            <span className="text-muted-foreground text-xs">{t("admin.usage.unpriced")}</span>
          </>
        )}
        {progress !== undefined && (
          <Progress
            value={Math.min(progress, 100)}
            className={cn(
              "mt-1",
              progress >= 100 && "[&_[data-slot=progress-indicator]]:bg-destructive",
            )}
          />
        )}
        {note !== undefined && <span className="text-muted-foreground text-xs">{note}</span>}
      </CardContent>
    </Card>
  );
}

function TotalsTiles({ usage }: { usage: Usage }) {
  const { t, i18n } = useTranslation();

  // The month tile checks itself against the budget-alert threshold (limits of
  // the same response) — crossing it colors the value.
  const budget = usage.limits.ai_monthly_budget;
  const monthCost = usage.totals.month.cost;
  const overBudget = budget !== null && monthCost !== null && Number(monthCost) >= Number(budget);
  // A soft gauge under the month value visualizes how much of the budget alert is
  // spent — the note keeps the exact numbers.
  const monthPct =
    budget !== null && Number(budget) > 0 && monthCost !== null
      ? (Number(monthCost) / Number(budget)) * 100
      : undefined;
  const monthNote =
    budget === null
      ? undefined
      : overBudget
        ? t("admin.usage.alertThresholdOver", { value: formatMoney(budget, i18n.language) })
        : t("admin.usage.alertThreshold", { value: formatMoney(budget, i18n.language) });

  return (
    <div className="flex flex-wrap gap-4">
      <SpendTile
        label={t("admin.usage.windows.week")}
        tokens={usage.totals.week.tokens}
        cost={usage.totals.week.cost}
      />
      <SpendTile
        label={t("admin.usage.windows.month")}
        tokens={usage.totals.month.tokens}
        cost={monthCost}
        tone={overBudget ? "warning" : undefined}
        progress={monthPct}
        note={monthNote}
      />
      <SpendTile
        label={t("admin.usage.windows.year")}
        tokens={usage.totals.year.tokens}
        cost={usage.totals.year.cost}
      />
    </div>
  );
}

const TOKENS_PER_MILLION = 1_000_000;
/** Raw tokens → the millions shown in the field (empty when there is no limit). */
function toMillions(tokens: number | null): string {
  return tokens === null ? "" : String(tokens / TOKENS_PER_MILLION);
}
/** The field's millions → raw tokens for the wire (empty/zero → no limit). */
function fromMillions(millions: string): number | null {
  const value = Number(millions);
  return millions && value > 0 ? Math.round(value * TOKENS_PER_MILLION) : null;
}

/** The limits section writes through PATCH /admin/settings (Owner). */
function LimitsCard() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const settings = useQuery({ queryKey: platformKeys.settings, queryFn: getPlatformSettings });
  const [draft, setDraft] = useState<{
    agents: string;
    chat: string;
    budget: string;
    alert: boolean;
  } | null>(null);

  const save = useMutation({
    mutationFn: patchPlatformSettings,
    onSuccess: (fresh) => {
      queryClient.setQueryData(platformKeys.settings, fresh);
      void queryClient.invalidateQueries({ queryKey: ["admin", "ai", "usage"] });
      setDraft(null);
      toast.success(t("admin.platform.saved"));
    },
    onError: (error) => void toastApiError(error, t("admin.platform.saveFailed")),
  });

  // Settings load independently of the usage totals above, so this card must hold
  // its height while they arrive — a null here drags the panels below it upward.
  if (settings.isPending) return <LimitsCardSkeleton />;
  if (settings.isError)
    return (
      <Card id="limits" className="scroll-mt-6 shadow-2xs">
        <SectionHeader icon={SlidersHorizontalIcon} title={t("admin.usage.limits")} divider />
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
  // Token budgets are entered in millions — a weekly ceiling is a seven-figure
  // number, and typing six zeros is a chore. The wire stays raw tokens.
  const values = draft ?? {
    agents: toMillions(row.agent_weekly_token_budget),
    chat: toMillions(row.chat_weekly_token_budget),
    budget: row.ai_monthly_budget ?? "",
    alert: row.ai_budget_alert_enabled,
  };

  // Each field's guidance lives behind an info icon beside its label, off the
  // permanent layout — the row stays a compact strip of inputs, the "M" unit
  // resting inside each field.
  const numberField = (id: string, label: string, key: "agents" | "chat", hint: string) => (
    <div className="flex flex-col gap-1.5">
      {/* InfoHint sits beside the label, not inside it — a <label htmlFor> must
          not also wrap a button, or it labels two controls at once. */}
      <div className="flex items-center gap-1.5">
        <Label htmlFor={id}>{label}</Label>
        <InfoHint text={hint} />
      </div>
      <div className="relative w-full">
        <Input
          id={id}
          type="number"
          min={0}
          step="any"
          className="pr-12"
          value={values[key]}
          placeholder={t("admin.usage.noLimit")}
          onChange={(e) => {
            setDraft({ ...values, [key]: e.target.value });
          }}
        />
        <span className="text-muted-foreground pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-xs">
          {t("admin.usage.millionsUnit")}
        </span>
      </div>
      {/* A unit caption under each field settles "tokens or money?" at a glance —
          the token ceilings read in tokens, the budget alert in dollars. The
          caption stays on one line and sizes the column, so the input above
          stretches to exactly its width. */}
      <span className="text-muted-foreground text-xs whitespace-nowrap">
        {t("admin.usage.unitTokens")}
      </span>
    </div>
  );

  return (
    <Card id="limits" className="scroll-mt-6 shadow-2xs">
      <SectionHeader icon={SlidersHorizontalIcon} title={t("admin.usage.limits")} divider />
      <CardContent className="flex flex-wrap items-end gap-8">
        {numberField(
          "limit-agents",
          t("admin.usage.agentBudget"),
          "agents",
          t("admin.usage.agentBudgetHint"),
        )}
        {numberField(
          "limit-chat",
          t("admin.usage.chatBudget"),
          "chat",
          t("admin.usage.chatBudgetHint"),
        )}
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center gap-1.5">
            <Label htmlFor="limit-budget">{t("admin.usage.monthlyBudget")}</Label>
            <InfoHint text={t("admin.usage.monthlyBudgetHint")} />
          </div>
          <div className="flex items-center gap-2">
            <div className="relative flex-1">
              <span className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-xs">
                $
              </span>
              <Input
                id="limit-budget"
                type="number"
                min={1}
                className="pl-7"
                value={values.budget}
                placeholder={t("admin.usage.noLimit")}
                onChange={(e) => {
                  setDraft({ ...values, budget: e.target.value });
                }}
              />
            </div>
            <Switch
              checked={values.alert}
              disabled={!values.budget}
              onCheckedChange={(alert) => {
                setDraft({ ...values, alert });
              }}
            />
          </div>
          <span className="text-muted-foreground text-xs whitespace-nowrap">
            {t("admin.usage.unitMoney")}
          </span>
        </div>
        {/* items-end aligns the button's bottom with the inputs — no spacer label. */}
        <Button
          size="sm"
          className="h-9"
          disabled={draft === null || save.isPending}
          onClick={() => {
            save.mutate({
              agent_weekly_token_budget: fromMillions(values.agents),
              chat_weekly_token_budget: fromMillions(values.chat),
              ai_monthly_budget: values.budget || null,
              ai_budget_alert_enabled: values.budget ? values.alert : false,
            });
          }}
        >
          {t("admin.platform.save")}
        </Button>
      </CardContent>
    </Card>
  );
}

/** Mirrors LimitsCard's strip of inputs so the placeholder occupies its height. */
function LimitsCardSkeleton() {
  const { t } = useTranslation();
  const field = (width: string) => (
    <div className="flex flex-col gap-1.5">
      <Skeleton className="h-4 w-28" />
      <Skeleton className={`h-9 ${width}`} />
      <Skeleton className={`h-3 ${width}`} />
    </div>
  );

  return (
    <Card id="limits" className="scroll-mt-6 shadow-2xs">
      <SectionHeader icon={SlidersHorizontalIcon} title={t("admin.usage.limits")} divider />
      <CardContent className="flex flex-wrap items-end gap-8">
        {field("w-52")}
        {field("w-52")}
        {field("w-44")}
        <Skeleton className="h-9 w-20" />
      </CardContent>
    </Card>
  );
}

function ByModelCard({
  usage,
  days,
  onDaysChange,
}: {
  usage: Usage;
  days: (typeof MODEL_DAYS)[number];
  onDaysChange: (days: (typeof MODEL_DAYS)[number]) => void;
}) {
  const { t, i18n } = useTranslation();
  // by_model arrives as one array; it sorts and paginates client-side so a long
  // catalogue browses page by page, like the by-person table above. Default: the
  // costliest models first — the screen is a spend showcase.
  const {
    sorted,
    sort,
    toggle: toggleSort,
  } = useClientSort(usage.by_model, MODEL_SORT, { key: "cost", desc: true });
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState<PerPage>(SPEND_PER_PAGE);
  const total = sorted.length;
  const lastPage = Math.max(1, Math.ceil(total / perPage));
  const current = Math.min(page, lastPage);
  const rows = sorted.slice((current - 1) * perPage, current * perPage);
  return (
    <Card className="shadow-2xs">
      <SectionHeader icon={CpuIcon} title={t("admin.usage.byModel")}>
        <SelectField
          size="sm"
          className="w-32"
          options={MODEL_DAYS.map((value) => ({
            value: String(value),
            label: t("admin.usage.lastDays", { days: value }),
          }))}
          value={String(days)}
          onValueChange={(value) => {
            onDaysChange(Number(value) as (typeof MODEL_DAYS)[number]);
          }}
        />
      </SectionHeader>
      <CardContent className="flex flex-col gap-3">
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
                  label={t("admin.usage.columns.function")}
                  sortKey="function"
                  sort={sort}
                  onToggle={toggleSort}
                />
                <SortableHead
                  label={t("admin.usage.columns.requests")}
                  sortKey="requests"
                  sort={sort}
                  onToggle={toggleSort}
                />
                <SortableHead
                  label={t("admin.usage.columns.input")}
                  sortKey="input"
                  sort={sort}
                  onToggle={toggleSort}
                />
                <SortableHead
                  label={t("admin.usage.columns.output")}
                  sortKey="output"
                  sort={sort}
                  onToggle={toggleSort}
                />
                <SortableHead
                  label={t("admin.usage.columns.cost")}
                  sortKey="cost"
                  sort={sort}
                  onToggle={toggleSort}
                />
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row, index) => (
                <TableRow
                  key={`${row.display_name ?? "gone"}-${row.function}-${String(index)}`}
                  className="hover:bg-muted/40 h-12"
                >
                  <TruncateCell
                    className="max-w-[16rem] font-medium"
                    text={row.display_name ?? t("admin.usage.deletedModel")}
                  >
                    {row.display_name ?? t("admin.usage.deletedModel")}
                    {row.provider_name && (
                      <span className="text-muted-foreground ml-1 text-xs">
                        {row.provider_name}
                      </span>
                    )}
                  </TruncateCell>
                  <TruncateCell
                    className="text-muted-foreground max-w-[14rem] text-sm"
                    text={functionLabel(t, row.function)}
                  />
                  <TableCell className="tabular-nums">
                    {formatNumber(row.request_count, i18n.language)}
                  </TableCell>
                  <TableCell className="tabular-nums">
                    {formatTokens(row.input_tokens, i18n.language)}
                  </TableCell>
                  <TableCell className="tabular-nums">
                    {row.output_tokens ? formatTokens(row.output_tokens, i18n.language) : "—"}
                  </TableCell>
                  <TableCell className="tabular-nums">
                    {row.cost === null
                      ? t("admin.usage.unpriced")
                      : formatMoney(row.cost, i18n.language)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </DataTable>
        </TableFrame>
        {total > perPage && (
          <Pagination
            page={current}
            perPage={perPage}
            total={total}
            onPageChange={setPage}
            onPerPageChange={(next) => {
              setPerPage(next);
              setPage(1);
            }}
          />
        )}
      </CardContent>
    </Card>
  );
}
