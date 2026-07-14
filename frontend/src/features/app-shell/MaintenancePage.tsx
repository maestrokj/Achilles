import { WrenchIcon } from "lucide-react";
import { useSyncExternalStore, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { SystemScreen } from "@/components/SystemScreen";
import { Button } from "@/components/ui/button";
import { isMember } from "@/features/auth/roles";
import { useSession } from "@/features/auth/session-context";

import { isMaintenanceActive, subscribeMaintenance } from "./maintenance-store";

/** The full-screen stub members see while the Owner holds maintenance mode. */
function MaintenancePage() {
  const { t } = useTranslation();
  return (
    <SystemScreen
      icon={WrenchIcon}
      title={t("app.maintenance.title")}
      body={t("app.maintenance.body")}
    >
      <Button
        variant="outline"
        size="sm"
        onClick={() => {
          window.location.reload();
        }}
      >
        {t("app.maintenance.retry")}
      </Button>
    </SystemScreen>
  );
}

/** Members get the stub; Owner/Admin keep the app (they must switch it off),
 * and the anonymous login screen stays reachable. */
export function MaintenanceGate({ children }: { children: ReactNode }) {
  const active = useSyncExternalStore(subscribeMaintenance, isMaintenanceActive);
  const session = useSession();
  if (active && isMember(session.user?.role)) return <MaintenancePage />;
  return children;
}
