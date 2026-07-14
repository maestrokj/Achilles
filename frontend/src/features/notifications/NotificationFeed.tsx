import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BellIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { ListQuery } from "@/api/lists";
import { EmptyState } from "@/components/list-controls/EmptyState";
import { FacetSelect } from "@/components/list-controls/FacetSelect";
import { Pagination } from "@/components/list-controls/Pagination";
import { SearchInput } from "@/components/list-controls/SearchInput";
import { useListState } from "@/components/list-controls/useListState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

import { listNotifications, markRead, notificationKeys } from "./api";
import { severityDot } from "./severity";
import { SourceRefLink } from "./SourceRefLink";
import type { EventTypeKey, NotificationItem, Severity, Surface } from "./types";
import { isUnread } from "./unread";

const ALL = "all";
const SEVERITIES = ["info", "warning", "critical"] as const satisfies readonly Severity[];
/** The backend's period vocabulary (notifications/service.py PERIOD_WINDOWS). */
const PERIODS = ["24h", "7d", "30d"] as const;
/** URL-backed facets: type/severity are multi-pick (OR), period a single window,
 * `history` the deviation from the unread-only default (present ⇒ show all). */
const FACETS = ["type", "severity", "period", "history"] as const;

/** The inbox list with facets — shared by /inbox and /admin/notifications/inbox.
 * `surface` steers the source_ref deep links to admin or personal screens.
 * State lives in the URL (useListState), so refresh and shared links reproduce
 * the exact view — the same contract as the admin tables.
 * Wireframes: notification-feed.html#inbox (both surfaces). */
export function NotificationFeed({
  types,
  surface,
}: {
  types: readonly EventTypeKey[];
  surface: Surface;
}) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const list = useListState(FACETS);

  // Type and severity combine as OR (multi-pick, like the audit log); period is
  // a single window. Empty arrays mean "no filter", so the label never mutates.
  const typeFacet = list.facets.type;
  const severityFacet = list.facets.severity;
  const period = list.facets.period[0] ?? ALL;
  // "Unread" is the wireframe default — history is one switch away. The URL only
  // carries the deviation, so a plain link opens on the unread tab.
  const unreadOnly = list.facets.history.length === 0;

  const query: ListQuery = { page: list.page, per_page: list.perPage };
  if (typeFacet.length) query.type = typeFacet;
  if (severityFacet.length) query.severity = severityFacet;
  if (period !== ALL) query.period = period;
  if (unreadOnly) query.unread = "true";
  if (list.q) query.q = list.q;

  const feed = useQuery({
    queryKey: notificationKeys.feed(query),
    queryFn: () => listNotifications(query),
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["notifications"] });
  };
  const readOne = useMutation({ mutationFn: markRead, onSuccess: invalidate });

  // Facets (not the unread toggle) narrow the feed — an empty result then means
  // "no matches", offering a reset rather than the zero state.
  const isFiltered =
    typeFacet.length > 0 || severityFacet.length > 0 || period !== ALL || list.q !== "";
  // Reset clears the filters but keeps the unread/history mode — that switch is a
  // primary tab, not a filter chip.
  const resetFilters = () => {
    list.clearSearch();
    list.setFacet("type", []);
    list.setFacet("severity", []);
    list.setFacet("period", []);
  };

  // A card frame binds the toolbar, the divided list, and the pager into one
  // surface — the same chrome the rest of the app's lists wear. `gap-0 py-0`
  // hands spacing to the strips so rows can bleed to the rounded edges.
  return (
    <Card className="gap-0 py-0 shadow-2xs">
      <div className="flex flex-col gap-2.5 p-3">
        {/* Row 1 — the search box, with the unread toggle riding the far right. */}
        <div className="flex items-center gap-3">
          <SearchInput
            value={list.input}
            onChange={list.setInput}
            onClear={list.clearSearch}
            placeholder={t("notifications.facets.searchPlaceholder")}
          />
          <label className="text-muted-foreground ml-auto flex shrink-0 items-center gap-2 text-sm">
            <Switch
              checked={unreadOnly}
              onCheckedChange={(next) => {
                list.setFacet("history", next ? [] : ["1"]);
              }}
            />
            {t("notifications.facets.unreadOnly")}
          </label>
        </div>

        {/* Row 2 — the facets (type · severity · period). Each wears a fixed bold
            label that never mutates; the choice lives on ticks inside, matching
            the audit log. Type and severity are multi-pick (OR); period is a
            single window. The bulk "mark all read" lives in the page header. */}
        <div className="flex flex-wrap items-center gap-2">
          <FacetSelect
            label={t("notifications.facets.type")}
            options={types.map((item) => ({
              value: item,
              label: t(`notifications.types.${item}`),
            }))}
            selected={typeFacet}
            onToggle={(value) => {
              list.toggleFacet("type", value);
            }}
          />
          <FacetSelect
            label={t("notifications.facets.severity")}
            options={SEVERITIES.map((item) => ({
              value: item,
              label: t(`notifications.severities.${item}`),
            }))}
            selected={severityFacet}
            onToggle={(value) => {
              list.toggleFacet("severity", value);
            }}
          />
          <FacetSelect
            label={t("notifications.facets.period")}
            options={[
              { value: ALL, label: t("common.periods.all") },
              ...PERIODS.map((item) => ({ value: item, label: t(`common.periods.${item}`) })),
            ]}
            selected={[period]}
            onToggle={(value) => {
              list.setFacet("period", value === ALL ? [] : [value]);
            }}
          />
        </div>
      </div>

      <div className="border-border border-t">
        {feed.isPending ? (
          <div className="p-4">
            <Skeleton className="h-48 w-full" />
          </div>
        ) : feed.isError ? (
          <EmptyState
            variant="error"
            description={t("common.list.errorTitle")}
            onRetry={() => {
              void feed.refetch();
            }}
          />
        ) : feed.data.items.length === 0 ? (
          <EmptyState
            filtered={isFiltered}
            onReset={resetFilters}
            icon={BellIcon}
            description={unreadOnly ? t("notifications.emptyUnread") : t("notifications.empty")}
            action={
              unreadOnly
                ? {
                    label: t("notifications.showHistory"),
                    onClick: () => {
                      list.setFacet("history", ["1"]);
                    },
                  }
                : undefined
            }
          />
        ) : (
          <div className="divide-border divide-y">
            {feed.data.items.map((item) => (
              <FeedRow
                key={item.id}
                item={item}
                surface={surface}
                locale={i18n.language}
                onRead={() => {
                  readOne.mutate(item.id);
                }}
              />
            ))}
          </div>
        )}
      </div>

      {feed.data && feed.data.total > list.perPage && (
        <div className="border-border border-t p-3">
          <Pagination
            page={feed.data.page}
            perPage={list.perPage}
            total={feed.data.total}
            onPageChange={list.setPage}
            onPerPageChange={list.setPerPage}
          />
        </div>
      )}
    </Card>
  );
}

function FeedRow({
  item,
  surface,
  locale,
  onRead,
}: {
  item: NotificationItem;
  surface: Surface;
  locale: string;
  onRead: () => void;
}) {
  const { t } = useTranslation();
  const unread = isUnread(item);
  return (
    <div className="hover:bg-muted/40 flex items-start gap-3 px-4 py-3.5 transition-colors">
      <span className={severityDot(item.severity, !unread)} aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <p className={cn("text-sm", unread ? "font-medium" : "text-muted-foreground")}>
          {item.title}
          {item.dedup_count > 1 && (
            <Badge variant="secondary" className="ml-2">
              ×{item.dedup_count}
            </Badge>
          )}
        </p>
        {item.body && <p className="text-muted-foreground mt-0.5 text-sm">{item.body}</p>}
        <div className="text-muted-foreground mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
          <span>{t(`notifications.types.${item.event_type}`)}</span>
          <span aria-hidden="true">·</span>
          <span>{formatDateTime(item.last_seen_at ?? item.created_at, locale)}</span>
          <SourceRefLink item={item} surface={surface} />
        </div>
      </div>
      {unread && (
        <Button
          variant="ghost"
          size="sm"
          className="text-muted-foreground hover:text-foreground -my-1 shrink-0 text-xs"
          onClick={onRead}
        >
          {t("notifications.markRead")}
        </Button>
      )}
    </div>
  );
}
