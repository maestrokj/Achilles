import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";

import { getPrefs, notificationKeys } from "./api";
import { MarkAllReadButton } from "./MarkAllReadButton";
import { NotificationFeed } from "./NotificationFeed";

/** /inbox — the personal feed. The subscription switches live on their own
 * screen (/inbox/settings), reached by the gear here.
 * The facet types are the prefs slice — the backend decides what's visible.
 * Wireframe: web-app/_wireframes/notification-feed.html#inbox. */
export function InboxPage() {
  const { t } = useTranslation();
  const prefs = useQuery({ queryKey: notificationKeys.prefs, queryFn: getPrefs });
  const types = prefs.data?.items.map((item) => item.event_type) ?? [];

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-6 py-8">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">{t("notifications.title")}</h1>
        <div className="flex shrink-0 items-center gap-2">
          <Button variant="outline" size="sm" render={<Link to="/inbox/settings" />}>
            {t("notifications.settingsTitle")}
          </Button>
          <MarkAllReadButton />
        </div>
      </div>
      <NotificationFeed types={types} surface="app" />
    </div>
  );
}
