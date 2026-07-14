import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ChevronDownIcon, ScrollTextIcon } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import type { ParseKeys } from "i18next";

import { LIVE_STALE_TIME } from "@/api/freshness";
import { DataTable, TableFrame, TruncateCell } from "@/components/list-controls/DataTable";
import { EmptyState } from "@/components/list-controls/EmptyState";
import { FacetSelect } from "@/components/list-controls/FacetSelect";
import { Pagination } from "@/components/list-controls/Pagination";
import { SearchInput } from "@/components/list-controls/SearchInput";
import { TableSkeleton } from "@/components/list-controls/TableSkeleton";
import { buildListQuery, useListState } from "@/components/list-controls/useListState";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ForbiddenPage } from "@/features/admin/ForbiddenPage";
import {
  listUsers,
  USER_SUGGEST_LIMIT,
  USER_SUGGEST_PAGE_SIZE,
  usersKeys,
} from "@/features/admin/users/api";
import { isOwner } from "@/features/auth/roles";
import { useSession } from "@/features/auth/session-context";
import { auditActionLabel } from "@/lib/badges";
import { formatWhen } from "@/lib/format";

import { listAudit, securityKeys } from "./api";

import type { AuditEntry } from "./types";

const FACETS = ["action_group", "actor_id", "period"] as const;

/** Period presets: 1h / 24h / 7d / 30d, plus the explicit "all". */
const PERIOD_WINDOWS_MS: Record<string, number | undefined> = {
  "1h": 60 * 60 * 1000,
  "24h": 24 * 60 * 60 * 1000,
  "7d": 7 * 24 * 60 * 60 * 1000,
  "30d": 30 * 24 * 60 * 60 * 1000,
};
const PERIOD_CHOICES = ["1h", "24h", "7d", "30d", "all"] as const;
/** The wireframe ships the Period facet pre-applied — the journal opens on the last week. */
const DEFAULT_PERIOD = "7d";

/** Admin · Audit Log: the read-only security journal (Owner only).
 * Wireframe: admin-panel/_wireframes/audit-log.html. */
export function AuditLogPage() {
  const session = useSession();
  if (!isOwner(session.user?.role)) return <ForbiddenPage />;
  return <AuditLogContent />;
}

function AuditLogContent() {
  const { t, i18n } = useTranslation();
  const list = useListState(FACETS);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const rawPeriod = list.facets["period"][0];
  const period =
    rawPeriod && (PERIOD_CHOICES as readonly string[]).includes(rawPeriod)
      ? rawPeriod
      : DEFAULT_PERIOD;

  // The query key carries the period choice; the absolute `from` is resolved
  // inside queryFn so `Date.now()` never runs during render.
  const query = buildListQuery(list);
  const entries = useQuery({
    queryKey: securityKeys.audit(query),
    queryFn: () => {
      const params = { ...query };
      delete params["period"];
      const window = PERIOD_WINDOWS_MS[period];
      if (window !== undefined) {
        params["from"] = new Date(Date.now() - window).toISOString();
      }
      return listAudit(params);
    },
    placeholderData: keepPreviousData,
    // A live journal filled by side effects of actions on *other* screens — no
    // mutation invalidates this key, so refetch on every visit (see freshness.ts).
    staleTime: LIVE_STALE_TIME,
  });

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
      <h1 className="text-2xl font-semibold tracking-tight">{t("admin.nav.auditLog")}</h1>
      <div className="flex flex-wrap items-center gap-2">
        <SearchInput
          value={list.input}
          onChange={list.setInput}
          onClear={list.clearSearch}
          placeholder={t("admin.audit.searchPlaceholder")}
        />
        <ActorFacet
          selected={list.facets["actor_id"] ?? []}
          onChange={(values) => {
            list.setFacet("actor_id", values);
          }}
        />
        <FacetSelect
          label={t("admin.audit.actionGroup")}
          options={(entries.data?.groups ?? []).map((value) => {
            // The catalog comes from the server; a group without a locale entry
            // renders as its raw slug rather than a broken key.
            const key = `admin.audit.groups.${value}`;
            return { value, label: i18n.exists(key) ? t(key as ParseKeys) : value };
          })}
          selected={list.facets["action_group"] ?? []}
          onToggle={(value) => {
            list.toggleFacet("action_group", value);
          }}
        />
        <FacetSelect
          label={t("admin.audit.period")}
          options={PERIOD_CHOICES.map((value) => ({
            value,
            label: t(`common.periods.${value}`),
          }))}
          selected={[period]}
          onToggle={(value) => {
            list.setFacet("period", [value]);
          }}
        />
      </div>

      {entries.isPending ? (
        <TableSkeleton cols={6} />
      ) : entries.isError ? (
        <EmptyState
          variant="error"
          onRetry={() => {
            void entries.refetch();
          }}
        />
      ) : entries.data.items.length === 0 ? (
        <EmptyState
          filtered={list.isFiltered}
          onReset={list.clearFilters}
          icon={ScrollTextIcon}
          title={t("admin.audit.emptyTitle")}
          description={t("admin.audit.emptyHint")}
        />
      ) : (
        <>
          <TableFrame>
            <DataTable>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("admin.audit.columns.when")}</TableHead>
                  <TableHead>{t("admin.audit.columns.actor")}</TableHead>
                  <TableHead>{t("admin.audit.columns.action")}</TableHead>
                  <TableHead>{t("admin.audit.columns.target")}</TableHead>
                  <TableHead>{t("admin.audit.columns.result")}</TableHead>
                  <TableHead>{t("admin.audit.columns.ip")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {entries.data.items.map((entry) => (
                  <AuditRow
                    key={entry.id}
                    entry={entry}
                    locale={i18n.language}
                    expanded={expandedId === entry.id}
                    onToggle={() => {
                      setExpandedId((current) => (current === entry.id ? null : entry.id));
                    }}
                  />
                ))}
              </TableBody>
            </DataTable>
          </TableFrame>
          <Pagination
            page={entries.data.page}
            perPage={list.perPage}
            total={entries.data.total}
            onPageChange={list.setPage}
            onPerPageChange={list.setPerPage}
          />
        </>
      )}
    </div>
  );
}

/** One journal entry: the summary row expands into the full record
 * (meta · user agent · full target) — wireframe legend 2. */
function AuditRow({
  entry,
  locale,
  expanded,
  onToggle,
}: {
  entry: AuditEntry;
  locale: string;
  expanded: boolean;
  onToggle: () => void;
}) {
  const { t, i18n } = useTranslation();
  const actionLabel = auditActionLabel(entry.action, t, i18n);
  return (
    <>
      <TableRow
        className="hover:bg-muted/40 h-12 cursor-pointer"
        aria-expanded={expanded}
        onClick={onToggle}
      >
        <TableCell className="text-muted-foreground text-sm tabular-nums">
          <span className="flex items-center gap-1.5">
            <ChevronDownIcon
              aria-hidden="true"
              className={`text-muted-foreground/60 size-3.5 shrink-0 transition-transform ${
                expanded ? "" : "-rotate-90"
              }`}
            />
            {formatWhen(entry.created_at, locale)}
          </span>
        </TableCell>
        <TruncateCell
          className="max-w-[16rem] text-sm"
          text={entry.actor_email ?? String(entry.actor_id ?? "—")}
        >
          {entry.actor_id !== null ? (
            <Link
              className="hover:underline"
              to={`/admin/users/${String(entry.actor_id)}`}
              onClick={(event) => {
                event.stopPropagation();
              }}
            >
              {entry.actor_email ?? entry.actor_id}
            </Link>
          ) : (
            "—"
          )}
        </TruncateCell>
        <TruncateCell className="max-w-[14rem] text-sm" text={actionLabel} />
        <TruncateCell
          className="text-muted-foreground max-w-[12rem] text-sm"
          text={entry.target_type ? `${entry.target_type} ${entry.target_id ?? ""}` : "—"}
        />
        <TableCell>
          <span className="flex items-center gap-1.5 text-sm">
            <span
              aria-hidden="true"
              className={`size-1.5 rounded-full ${
                entry.result === "success" ? "bg-success" : "bg-destructive"
              }`}
            />
            {entry.result}
          </span>
        </TableCell>
        <TableCell className="text-muted-foreground text-sm tabular-nums">
          {entry.ip ?? "—"}
        </TableCell>
      </TableRow>
      {expanded && (
        <TableRow className="bg-muted/30 hover:bg-muted/30">
          <TableCell colSpan={6} className="py-3">
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
              <dt className="text-muted-foreground text-xs font-medium">
                {t("admin.audit.columns.action")}
              </dt>
              <dd className="min-w-0 break-all">
                <code className="text-xs">{entry.action}</code>
              </dd>
              <dt className="text-muted-foreground text-xs font-medium">
                {t("admin.audit.columns.target")}
              </dt>
              <dd className="min-w-0 break-all">
                {entry.target_type ? `${entry.target_type} ${entry.target_id ?? ""}` : "—"}
              </dd>
              <dt className="text-muted-foreground text-xs font-medium">
                {t("admin.audit.detail.userAgent")}
              </dt>
              <dd className="min-w-0 break-all">{entry.user_agent ?? "—"}</dd>
              <dt className="text-muted-foreground text-xs font-medium">
                {t("admin.audit.detail.meta")}
              </dt>
              <dd className="min-w-0">
                {entry.meta && Object.keys(entry.meta).length > 0 ? (
                  <pre className="bg-muted overflow-x-auto rounded-md p-2 text-xs">
                    {JSON.stringify(entry.meta, null, 2)}
                  </pre>
                ) : (
                  "—"
                )}
              </dd>
            </dl>
          </TableCell>
        </TableRow>
      )}
    </>
  );
}

/** The Actor facet: pick one employee by name/email — the suggestion search
 * rides the backend, as in CreateKeyDialog; the choice lands in ?actor_id=. */
function ActorFacet({
  selected,
  onChange,
}: {
  selected: string[];
  onChange: (values: string[]) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  // Remembered label of the picked user — after a URL-restored selection only
  // the id is known, and the badge count still marks the facet as active.
  const [pickedLabel, setPickedLabel] = useState<string | null>(null);

  const search = input.trim();
  const usersQuery = { page: 1, per_page: USER_SUGGEST_PAGE_SIZE, q: search };
  const users = useQuery({
    queryKey: usersKeys.list(usersQuery),
    queryFn: () => listUsers(usersQuery),
    enabled: open && search.length >= 2,
    placeholderData: keepPreviousData,
  });
  const suggestions = (users.data?.items ?? []).slice(0, USER_SUGGEST_LIMIT);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger render={<Button variant="outline" />}>
        {t("admin.audit.columns.actor")}
        <ChevronDownIcon className="size-4" />
      </PopoverTrigger>
      <PopoverContent align="start" className="flex w-72 flex-col gap-2 p-2">
        {selected.length > 0 && (
          <div className="flex items-center justify-between gap-2 text-sm">
            <span className="text-muted-foreground min-w-0 truncate">
              {pickedLabel ?? `#${selected[0]}`}
            </span>
            <Button
              variant="ghost"
              size="sm"
              className="text-xs"
              onClick={() => {
                setPickedLabel(null);
                onChange([]);
              }}
            >
              {t("common.list.reset")}
            </Button>
          </div>
        )}
        <Input
          value={input}
          placeholder={t("admin.audit.actorSearchPlaceholder")}
          onChange={(event) => {
            setInput(event.target.value);
          }}
        />
        {search.length >= 2 && suggestions.length > 0 && (
          <div className="divide-border divide-y overflow-hidden rounded-lg border">
            {suggestions.map((user) => (
              <button
                key={user.id}
                type="button"
                className="hover:bg-muted/60 flex w-full flex-col items-start px-3 py-1.5 text-left transition-colors"
                onClick={() => {
                  setPickedLabel(user.email);
                  onChange([String(user.id)]);
                  setOpen(false);
                }}
              >
                <span className="text-sm font-medium">{user.full_name}</span>
                <span className="text-muted-foreground text-xs">{user.email}</span>
              </button>
            ))}
          </div>
        )}
        {search.length >= 2 && !users.isPending && suggestions.length === 0 && (
          <p className="text-muted-foreground px-1 text-xs">{t("common.list.empty")}</p>
        )}
      </PopoverContent>
    </Popover>
  );
}
