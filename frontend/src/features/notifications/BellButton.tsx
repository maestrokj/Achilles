import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BellIcon, SettingsIcon } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { TruncatedText } from "@/components/TruncatedText";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDateTime } from "@/lib/format";

import { listNotifications, markAllRead, markRead, notificationKeys } from "./api";
import { severityDot } from "./severity";
import { SourceRefLink } from "./SourceRefLink";
import type { NotificationItem, Surface } from "./types";
import { isUnread } from "./unread";
import { useEventStream } from "@/features/live/useEventStream";

import { useUnreadCount } from "./useUnreadCount";

const PANEL_SIZE = 8;
/** Smallest of the backend's fixed per_page choices — the panel trims to PANEL_SIZE. */
const MIN_PER_PAGE = 25;

/** The header bell: live unread badge + a dropdown tail of the feed.
 * The gear leads to `settingsPath` — the surface's notification settings.
 * Wireframes: web-app & admin-panel notification-feed.html#bell. */
export function BellButton({
  inboxPath,
  settingsPath,
}: {
  inboxPath: string;
  settingsPath: string;
}) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const surface = inboxPath.startsWith("/admin") ? "admin" : "app";
  useEventStream();
  const unread = useUnreadCount();
  const count = unread.data?.count ?? 0;

  const tail = useQuery({
    queryKey: notificationKeys.feed({ per_page: MIN_PER_PAGE }),
    queryFn: () => listNotifications({ per_page: MIN_PER_PAGE }),
    enabled: open,
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["notifications"] });
  };
  const readOne = useMutation({ mutationFn: markRead, onSuccess: invalidate });
  const readAll = useMutation({ mutationFn: markAllRead, onSuccess: invalidate });

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={
          <Button
            variant="ghost"
            size="icon-sm"
            className="relative"
            aria-label={t("notifications.bell")}
          />
        }
      >
        <BellIcon />
        {count > 0 && (
          <span className="bg-primary text-primary-foreground absolute -top-0.5 -right-0.5 flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] leading-none font-semibold tabular-nums">
            {count > 99 ? "99+" : count}
          </span>
        )}
      </PopoverTrigger>
      <PopoverContent align="end" className="w-96 p-0">
        <div className="border-border flex items-center justify-between border-b px-3 py-2">
          <span className="text-sm font-semibold">{t("notifications.title")}</span>
          <span className="flex items-center gap-0.5">
            <Button
              variant="ghost"
              size="sm"
              className="text-muted-foreground h-7 text-xs"
              disabled={count === 0 || readAll.isPending}
              onClick={() => {
                readAll.mutate();
              }}
            >
              {t("notifications.readAll")}
            </Button>
            <Button
              variant="ghost"
              size="icon-sm"
              className="text-muted-foreground h-7 w-7"
              aria-label={t("notifications.settings")}
              title={t("notifications.settings")}
              render={
                <Link
                  to={settingsPath}
                  onClick={() => {
                    setOpen(false);
                  }}
                />
              }
            >
              <SettingsIcon />
            </Button>
          </span>
        </div>

        <div className="max-h-96 overflow-y-auto">
          {tail.isPending ? (
            <Skeleton className="m-3 h-24" />
          ) : tail.isError || tail.data.items.length === 0 ? (
            <p className="text-muted-foreground px-3 py-6 text-center text-sm">
              {t("notifications.empty")}
            </p>
          ) : (
            tail.data.items.slice(0, PANEL_SIZE).map((item) => (
              <PanelRow
                key={item.id}
                item={item}
                surface={surface}
                locale={i18n.language}
                onRead={() => {
                  readOne.mutate(item.id);
                }}
                onNavigate={() => {
                  setOpen(false);
                }}
              />
            ))
          )}
        </div>

        <div className="border-border border-t px-3 py-2 text-center">
          <Link
            to={inboxPath}
            className="text-muted-foreground hover:text-foreground text-xs underline underline-offset-4"
            onClick={() => {
              setOpen(false);
            }}
          >
            {t("notifications.showAll")}
          </Link>
        </div>
      </PopoverContent>
    </Popover>
  );
}

function PanelRow({
  item,
  surface,
  locale,
  onRead,
  onNavigate,
}: {
  item: NotificationItem;
  surface: Surface;
  locale: string;
  onRead: () => void;
  onNavigate: () => void;
}) {
  // A div, not a <button>: the source_ref deep link nests inside the clickable row.
  const unread = isUnread(item);
  return (
    <div
      role="button"
      tabIndex={0}
      className="hover:bg-muted/50 flex w-full cursor-pointer items-start gap-2 px-3 py-2 text-left"
      onClick={onRead}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") onRead();
      }}
    >
      <span className={severityDot(item.severity, !unread)} aria-hidden="true" />
      <span className="min-w-0 flex-1">
        <TruncatedText
          tooltip={item.title}
          className={unread ? "text-sm font-medium" : "text-muted-foreground text-sm"}
        >
          {item.title}
          {item.dedup_count > 1 && (
            <span className="text-muted-foreground font-normal"> ×{item.dedup_count}</span>
          )}
        </TruncatedText>
        <span className="text-muted-foreground block text-xs">
          {formatDateTime(item.last_seen_at ?? item.created_at, locale)}
        </span>
        <SourceRefLink
          item={item}
          surface={surface}
          className="block truncate"
          onNavigate={() => {
            // Acting on the link is acting on the notification — mark it read,
            // then close the panel (stopPropagation suppressed the row's onRead).
            onRead();
            onNavigate();
          }}
        />
      </span>
    </div>
  );
}
