import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { MarkAllReadButton } from "@/features/notifications/MarkAllReadButton";
import { NotificationFeed } from "@/features/notifications/NotificationFeed";
import { EVENT_TYPE_KEYS } from "@/features/notifications/types";

/** /admin/notifications/inbox — the admin's full feed with facets.
 * Wireframe: admin-panel/_wireframes/notification-feed.html#inbox. */
export function AdminInboxPage() {
  const { t } = useTranslation();
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
      <div className="flex items-center gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">{t("notifications.title")}</h1>
        <span className="flex-1" />
        <Button variant="outline" size="sm" render={<Link to="/admin/notifications" />}>
          {t("admin.notifications.toSettings")}
        </Button>
        <MarkAllReadButton />
      </div>
      <NotificationFeed types={EVENT_TYPE_KEYS} surface="admin" />
    </div>
  );
}
