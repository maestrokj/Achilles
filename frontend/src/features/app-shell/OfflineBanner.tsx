import { WifiOffIcon } from "lucide-react";
import { useSyncExternalStore } from "react";
import { useTranslation } from "react-i18next";

import { isOnline, subscribeOnline } from "./online-store";

/** Ambient top strip shown while the browser reports no network. The screen
 * underneath stays live; react-query's refetchOnReconnect refreshes data once
 * the connection returns and the banner clears itself. */
export function OfflineBanner() {
  const { t } = useTranslation();
  const online = useSyncExternalStore(subscribeOnline, isOnline);
  if (online) return null;

  return (
    <div className="pointer-events-none fixed inset-x-0 top-4 z-50 flex justify-center px-4">
      <div
        role="status"
        className="border-border/60 bg-card/80 text-foreground animate-in fade-in slide-in-from-top-2 pointer-events-auto flex max-w-[calc(100vw-2rem)] items-center gap-3 rounded-full border py-2 pr-5 pl-3 text-sm shadow-lg backdrop-blur-md duration-300"
      >
        <span className="bg-warning/15 flex size-7 shrink-0 items-center justify-center rounded-full">
          <WifiOffIcon className="text-warning size-4" aria-hidden="true" />
        </span>
        <span>
          <span className="font-medium">{t("app.offline.title")}</span>
          <span className="text-muted-foreground hidden sm:inline">
            {" "}
            — {t("app.offline.message")}
          </span>
        </span>
      </div>
    </div>
  );
}
