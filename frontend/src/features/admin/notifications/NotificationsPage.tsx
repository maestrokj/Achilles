import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowUpRightIcon,
  BellIcon,
  CheckIcon,
  InfoIcon,
  LockIcon,
  MailIcon,
  TriangleAlertIcon,
  WebhookIcon,
} from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { toast } from "@/lib/toast";

import { toastApiError } from "@/api/errors";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { TruncatedText } from "@/components/TruncatedText";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { EmptyState } from "@/components/list-controls/EmptyState";
import { DataTable, TableFrame } from "@/components/list-controls/DataTable";
import { RowActions } from "@/components/list-controls/RowActions";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { getSmtpSettings, platformKeys } from "@/features/admin/platform/api";
import { StatusBadge } from "@/features/admin/platform/StatusBadge";
import { isOwner } from "@/features/auth/roles";
import { useSession } from "@/features/auth/session-context";
import {
  deleteChannel,
  listChannels,
  listNotifications,
  listRoutes,
  markRead,
  notificationKeys,
  patchChannel,
  patchRoutes,
  testChannel,
} from "@/features/notifications/api";
import { severityDot, severityTone } from "@/features/notifications/severity";
import { ORG_TYPE_KEYS, type Channel, type RouteCell } from "@/features/notifications/types";
import { isUnread } from "@/features/notifications/unread";
import { formatWhen } from "@/lib/format";
import { cn } from "@/lib/utils";

import type { LucideIcon } from "lucide-react";
import type { TFunction } from "i18next";

import { AddWebhookDialog } from "./AddWebhookDialog";

/** How many of the freshest notifications the settings screen previews. */
const RECENT_LIMIT = 5;

/** A channel's display name: built-ins are labelled by kind, custom ones by name. */
function channelLabel(channel: Channel, t: TFunction): string {
  return channel.is_builtin ? t(`admin.notifications.builtin.${channel.kind}`) : channel.name;
}

/** The leading glyph of a channel row — one per delivery surface. */
const CHANNEL_ICON: Record<Channel["kind"], LucideIcon> = {
  in_app: BellIcon,
  email: MailIcon,
  webhook: WebhookIcon,
};

/** Backend webhook-test tokens (webhooks.py) → human text. Exact tokens
 * ("no_url", "blocked_host", "http_502") map directly; the reachability failures
 * carry a raw suffix ("dns: …", "network: …") folded into one "unreachable"
 * line. A genuinely unknown token still falls through as-is. */
function webhookTestError(raw: string | null, t: TFunction): string {
  if (raw === null) return t("admin.platform.slack.testErrorUnknown");
  if (raw === "no_url") return t("admin.notifications.testErrors.no_url");
  if (raw === "bad_scheme") return t("admin.notifications.testErrors.bad_scheme");
  if (raw === "bad_host") return t("admin.notifications.testErrors.bad_host");
  if (raw === "blocked_host") return t("admin.notifications.testErrors.blocked_host");
  const status = /^http_(\d{3})$/.exec(raw);
  if (status) return t("admin.notifications.testErrors.http", { status: status[1] });
  if (raw.startsWith("dns:") || raw.startsWith("network:"))
    return t("admin.notifications.testErrors.unreachable");
  return raw;
}

/** The small section heading shared by every card on the page. */
function SectionTitle({ children }: { children: React.ReactNode }) {
  return <h2 className="text-sm font-semibold">{children}</h2>;
}

/** /admin/notifications — channels · the routing matrix · a recent-feed preview.
 * Owner edits, Admin reads. Wireframe: admin-panel/_wireframes/notifications.html. */
export function NotificationsPage() {
  const { t } = useTranslation();
  const session = useSession();
  const readOnly = session.status !== "authenticated" || !isOwner(session.user.role);

  return (
    <TooltipProvider delay={200}>
      <div className="mx-auto flex w-full max-w-4xl flex-col gap-6">
        <div className="animate-in fade-in slide-in-from-bottom-1 flex items-center gap-3 duration-500">
          <h1 className="text-2xl font-semibold tracking-tight">{t("admin.nav.notifications")}</h1>
          {readOnly && <Badge variant="secondary">{t("admin.platform.readOnly")}</Badge>}
        </div>
        <ChannelsCard readOnly={readOnly} />
        <RoutesCard readOnly={readOnly} />
        <RecentCard />
      </div>
    </TooltipProvider>
  );
}

function ChannelsCard({ readOnly }: { readOnly: boolean }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const channels = useQuery({ queryKey: notificationKeys.channels, queryFn: listChannels });
  // The email channel rides the org SMTP — the chip warns when nothing can go out.
  const smtp = useQuery({ queryKey: platformKeys.smtp, queryFn: getSmtpSettings });
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<Channel | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Channel | null>(null);

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: notificationKeys.channels });
    void queryClient.invalidateQueries({ queryKey: notificationKeys.routes });
  };
  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      patchChannel(id, { enabled }),
    onSuccess: invalidate,
    onError: (error) => void toastApiError(error, t("admin.platform.saveFailed")),
  });
  const remove = useMutation({
    mutationFn: deleteChannel,
    onSuccess: () => {
      setDeleteTarget(null);
      invalidate();
    },
    onError: (error) => void toastApiError(error, t("admin.platform.saveFailed")),
  });
  const test = useMutation({
    mutationFn: testChannel,
    onSuccess: (result) => {
      invalidate();
      if (result.ok) toast.success(t("admin.notifications.testOk"));
      else {
        toast.error(
          t("admin.notifications.testFailed", { error: webhookTestError(result.error, t) }),
        );
      }
    },
    onError: (error) => void toastApiError(error, t("admin.platform.saveFailed")),
  });

  return (
    <Card className="animate-in fade-in duration-500">
      <CardContent className="flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <SectionTitle>{t("admin.notifications.channels")}</SectionTitle>
          {!readOnly && (
            <Button
              size="sm"
              onClick={() => {
                setDialogOpen(true);
              }}
            >
              {t("admin.notifications.addWebhook")}
            </Button>
          )}
        </div>
        <div className="flex flex-col gap-1.5">
          {channels.isPending ? (
            <Skeleton className="h-24 w-full" />
          ) : channels.isError ? (
            <EmptyState
              variant="error"
              description={t("common.list.errorTitle")}
              onRetry={() => {
                void channels.refetch();
              }}
            />
          ) : (
            channels.data.items.map((channel) => (
              <ChannelRow
                key={channel.id}
                channel={channel}
                readOnly={readOnly}
                smtpAvailable={channel.kind === "email" ? (smtp.data?.is_available ?? null) : null}
                onToggle={(enabled) => {
                  toggle.mutate({ id: channel.id, enabled });
                }}
                onEdit={() => {
                  setEditTarget(channel);
                }}
                onDelete={() => {
                  setDeleteTarget(channel);
                }}
                onTest={() => {
                  test.mutate(channel.id);
                }}
              />
            ))
          )}
        </div>
        <AddWebhookDialog open={dialogOpen} onOpenChange={setDialogOpen} onCreated={invalidate} />
        <AddWebhookDialog
          open={editTarget !== null}
          onOpenChange={(next) => {
            if (!next) setEditTarget(null);
          }}
          onCreated={invalidate}
          channel={editTarget}
        />
        <ConfirmDialog
          open={deleteTarget !== null}
          onOpenChange={(next) => {
            if (!next) setDeleteTarget(null);
          }}
          title={t("admin.notifications.deleteTitle")}
          description={t("admin.notifications.deleteDescription", {
            name: deleteTarget?.name ?? "",
          })}
          confirmLabel={t("admin.notifications.remove")}
          destructive
          pending={remove.isPending}
          onConfirm={() => {
            if (deleteTarget) remove.mutate(deleteTarget.id);
          }}
        />
      </CardContent>
    </Card>
  );
}

function ChannelRow({
  channel,
  readOnly,
  smtpAvailable,
  onToggle,
  onEdit,
  onDelete,
  onTest,
}: {
  channel: Channel;
  readOnly: boolean;
  /** Email channel only: is the org SMTP ready to send (null while loading / for other kinds). */
  smtpAvailable: boolean | null;
  onToggle: (enabled: boolean) => void;
  onEdit: () => void;
  onDelete: () => void;
  onTest: () => void;
}) {
  const { t } = useTranslation();
  const label = channelLabel(channel, t);
  const Icon = CHANNEL_ICON[channel.kind];
  return (
    <div className="border-border/60 flex min-h-12 items-center gap-3 rounded-lg border px-3 py-2">
      <span className="bg-secondary text-muted-foreground flex size-7 shrink-0 items-center justify-center rounded-lg">
        <Icon className="size-3.5" aria-hidden="true" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="flex items-center gap-2 text-sm font-medium">
          {label}
          {channel.is_builtin && channel.kind === "in_app" && (
            <LockIcon className="text-muted-foreground size-3.5" aria-hidden="true" />
          )}
          {channel.preset && <Badge variant="outline">{channel.preset}</Badge>}
        </p>
        {channel.url_mask && <p className="text-muted-foreground text-xs">{channel.url_mask}</p>}
      </div>
      {channel.kind === "email" &&
        smtpAvailable !== null &&
        (smtpAvailable ? (
          <span className="text-muted-foreground inline-flex items-center gap-1.5 text-xs">
            <CheckIcon className="text-success size-3.5" aria-hidden="true" />
            {t("admin.notifications.smtpConfigured")}
          </span>
        ) : (
          <Link
            to="/admin/platform#smtp"
            className="text-warning inline-flex items-center gap-1.5 text-xs font-medium underline-offset-4 hover:underline"
          >
            <TriangleAlertIcon className="size-3.5" aria-hidden="true" />
            {t("admin.notifications.smtpMissing")}
          </Link>
        ))}
      {channel.kind === "webhook" && channel.last_test_ok !== null && (
        <StatusBadge
          ok={channel.last_test_ok}
          labels={{
            ok: t("admin.notifications.testBadgeOk"),
            failed: t("admin.notifications.testBadgeFailed"),
            untested: "",
          }}
        />
      )}
      {!readOnly && channel.kind === "webhook" && (
        <RowActions
          actions={[
            { label: t("admin.notifications.test"), onSelect: onTest },
            { label: t("admin.notifications.edit"), onSelect: onEdit },
            { label: t("admin.notifications.remove"), onSelect: onDelete, destructive: true },
          ]}
        />
      )}
      <Switch
        checked={channel.enabled}
        disabled={readOnly || channel.kind === "in_app"}
        onCheckedChange={onToggle}
      />
    </div>
  );
}

function RoutesCard({ readOnly }: { readOnly: boolean }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const channels = useQuery({ queryKey: notificationKeys.channels, queryFn: listChannels });
  const routes = useQuery({ queryKey: notificationKeys.routes, queryFn: listRoutes });

  const patch = useMutation({
    mutationFn: patchRoutes,
    onSuccess: (fresh) => {
      queryClient.setQueryData(notificationKeys.routes, fresh);
    },
    onError: (error) => void toastApiError(error, t("admin.platform.saveFailed")),
  });

  const byCell = new Map<string, RouteCell>(
    (routes.data?.items ?? []).map((cell) => [
      `${cell.event_type}:${String(cell.channel_id)}`,
      cell,
    ]),
  );
  // Severity is a property of the event category — every cell of a row carries the same value.
  const severityByType = new Map<string, RouteCell["severity"]>(
    (routes.data?.items ?? []).map((cell) => [cell.event_type, cell.severity]),
  );

  return (
    <Card className="animate-in fade-in duration-500">
      <CardContent className="flex flex-col gap-3">
        <div className="flex items-center gap-1.5">
          <SectionTitle>{t("admin.notifications.routing")}</SectionTitle>
          <Tooltip>
            <TooltipTrigger
              render={
                <button
                  type="button"
                  aria-label={t("common.moreInfo")}
                  className="text-muted-foreground/50 hover:text-foreground inline-flex rounded-full p-0.5 transition-colors"
                />
              }
            >
              <InfoIcon className="size-3.5" />
            </TooltipTrigger>
            <TooltipContent className="max-w-xs leading-relaxed" align="start">
              {t("admin.notifications.routingHint")}
            </TooltipContent>
          </Tooltip>
        </div>
        {channels.isPending || routes.isPending ? (
          <Skeleton className="h-40 w-full" />
        ) : channels.isError || routes.isError ? (
          <EmptyState
            variant="error"
            description={t("common.list.errorTitle")}
            onRetry={() => {
              void channels.refetch();
              void routes.refetch();
            }}
          />
        ) : (
          <TableFrame variant="card">
            <DataTable>
              <TableHeader>
                <TableRow>
                  <TableHead className="py-2 pr-2">{t("admin.notifications.eventType")}</TableHead>
                  {channels.data.items.map((channel) => (
                    <TableHead key={channel.id} className="px-2 py-2 text-center">
                      {channelLabel(channel, t)}
                    </TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {ORG_TYPE_KEYS.map((eventType) => (
                  <TableRow key={eventType} className="hover:bg-muted/40 h-11">
                    <TableCell className="py-1.5 pr-2">
                      <span className="flex items-center gap-2">
                        {t(`notifications.types.${eventType}`)}
                        <SeverityChip severity={severityByType.get(eventType)} />
                      </span>
                    </TableCell>
                    {channels.data.items.map((channel) => {
                      // A missing row is an off-by-default cell — still toggleable
                      // (the toggle upserts it), never a dead dash.
                      const cell: RouteCell = byCell.get(`${eventType}:${String(channel.id)}`) ?? {
                        event_type: eventType,
                        channel_id: channel.id,
                        enabled: false,
                        locked: false,
                        severity: severityByType.get(eventType) ?? "info",
                      };
                      return (
                        <TableCell key={channel.id} className="px-2 py-1.5 text-center">
                          <RoutePill
                            cell={cell}
                            channelEnabled={channel.enabled}
                            readOnly={readOnly}
                            onToggle={() => {
                              patch.mutate([
                                {
                                  event_type: cell.event_type,
                                  channel_id: cell.channel_id,
                                  enabled: !cell.enabled,
                                },
                              ]);
                            }}
                          />
                        </TableCell>
                      );
                    })}
                  </TableRow>
                ))}
              </TableBody>
            </DataTable>
          </TableFrame>
        )}
      </CardContent>
    </Card>
  );
}

/** The quiet severity pill of a matrix row — the loudest catalog event of the category. */
function SeverityChip({ severity }: { severity: RouteCell["severity"] | undefined }) {
  const { t } = useTranslation();
  if (!severity) return null;
  const tone = severityTone(severity);
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-px text-[11px] ${tone}`}>
      {t(`notifications.severities.${severity}`)}
    </span>
  );
}

function RoutePill({
  cell,
  channelEnabled,
  readOnly,
  onToggle,
}: {
  cell: RouteCell;
  channelEnabled: boolean;
  readOnly: boolean;
  onToggle: () => void;
}) {
  const { t } = useTranslation();
  const pillBase = "inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs";
  if (cell.locked)
    return (
      <span
        className={`${pillBase} border-primary bg-primary text-primary-foreground`}
        title={t("admin.notifications.lockedHint")}
      >
        <LockIcon className="size-3.5 opacity-80" aria-hidden="true" />
        {t("admin.notifications.pillOn")}
      </span>
    );
  const paused = cell.enabled && !channelEnabled;
  const label = paused
    ? t("admin.notifications.pillPaused")
    : cell.enabled
      ? t("admin.notifications.pillOn")
      : t("admin.notifications.pillOff");
  const tone = cell.enabled
    ? paused
      ? "border-warning/40 bg-warning/10 text-warning"
      : "border-primary bg-primary text-primary-foreground hover:bg-primary/85"
    : "border-border text-muted-foreground";
  return (
    <button
      type="button"
      disabled={readOnly}
      className={`${pillBase} ${tone} transition-colors ${readOnly || cell.enabled ? "" : "hover:border-foreground/25"}`}
      onClick={onToggle}
    >
      {label}
    </button>
  );
}

/** The tail of the page: the five freshest notifications with a door to the full feed. */
function RecentCard() {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  // per_page is a fixed vocabulary (10/25/50/100) — fetch the smallest page and
  // show the freshest few.
  const query = { page: 1, per_page: 10 };
  const feed = useQuery({
    queryKey: notificationKeys.feed(query),
    queryFn: () => listNotifications(query),
  });
  const items = feed.data?.items.slice(0, RECENT_LIMIT) ?? [];

  // Same gesture as the inbox row: acknowledge in place — the dot fades, the
  // bell count drops. Invalidate the whole "notifications" tree (feed + unread).
  const readOne = useMutation({
    mutationFn: markRead,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["notifications"] }),
    onError: (error) => void toastApiError(error, t("admin.platform.saveFailed")),
  });

  return (
    <Card className="animate-in fade-in gap-0 py-0 duration-500">
      <div className="border-border/70 flex items-center border-b px-4 py-3">
        <SectionTitle>{t("admin.notifications.recentTitle")}</SectionTitle>
      </div>
      {feed.isPending ? (
        <div className="p-4">
          <Skeleton className="h-32 w-full" />
        </div>
      ) : feed.isError ? (
        <div className="p-4">
          <EmptyState
            variant="error"
            description={t("common.list.errorTitle")}
            onRetry={() => {
              void feed.refetch();
            }}
          />
        </div>
      ) : items.length === 0 ? (
        <p className="text-muted-foreground px-4 py-8 text-center text-sm">
          {t("notifications.empty")}
        </p>
      ) : (
        <div className="flex flex-col">
          {items.map((item) => {
            const unread = isUnread(item);
            return (
              <div
                key={item.id}
                className="border-border/60 flex items-start gap-3 border-b px-4 py-3 last:border-0"
              >
                <span className={severityDot(item.severity, !unread)} aria-hidden="true" />
                <div className="min-w-0 flex-1">
                  <TruncatedText
                    tooltip={item.title}
                    render={<p />}
                    className={cn("text-sm", unread ? "font-medium" : "text-muted-foreground")}
                  >
                    {item.title}
                    {item.dedup_count > 1 && (
                      <Badge variant="secondary" className="ml-2">
                        ×{item.dedup_count}
                      </Badge>
                    )}
                  </TruncatedText>
                  <p className="text-muted-foreground mt-0.5 text-xs">
                    {t(`notifications.types.${item.event_type}`)} ·{" "}
                    {formatWhen(item.last_seen_at ?? item.created_at, i18n.language)}
                  </p>
                </div>
                {unread && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-muted-foreground hover:text-foreground -my-1 shrink-0 text-xs"
                    disabled={readOne.isPending}
                    onClick={() => {
                      readOne.mutate(item.id);
                    }}
                  >
                    {t("notifications.markRead")}
                  </Button>
                )}
              </div>
            );
          })}
        </div>
      )}
      <Link
        to="/admin/notifications/inbox"
        className="text-muted-foreground hover:text-foreground hover:bg-muted/40 flex items-center justify-center gap-1.5 border-t px-4 py-2.5 text-xs font-medium transition-colors"
      >
        {t("notifications.showAll")}
        <ArrowUpRightIcon className="size-3.5" />
      </Link>
    </Card>
  );
}
