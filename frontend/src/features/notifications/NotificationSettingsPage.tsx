import { useTranslation } from "react-i18next";
import { useLocation } from "react-router-dom";

import { BackLink } from "@/components/BackLink";

import { NotificationPrefs } from "./NotificationPrefs";

/** Where the back link returns to. Callers reaching this screen from somewhere
 * other than the feed pass their origin via router state; without it we fall
 * back to the feed. */
type BackOrigin = { to: string; label: string };

/** /inbox/settings — the personal subscription switches, lifted off the feed
 * onto their own screen. The feed's gear, the sidebar account menu and the
 * profile's notifications card all lead here, so "back" follows the origin
 * passed in router state rather than a fixed destination.
 * Wireframe: auth-security/profile-account.html#notifications. */
export function NotificationSettingsPage() {
  const { t } = useTranslation();
  const origin = (useLocation().state as { back?: BackOrigin } | null)?.back;
  const back = origin ?? { to: "/inbox", label: t("notifications.title") };
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex w-full max-w-2xl flex-col gap-4 px-6 py-8">
        <div className="flex flex-col gap-1">
          <BackLink to={back.to} label={back.label} />
          <h1 className="text-2xl font-semibold tracking-tight">
            {t("notifications.settingsTitle")}
          </h1>
        </div>
        <NotificationPrefs />
      </div>
    </div>
  );
}
