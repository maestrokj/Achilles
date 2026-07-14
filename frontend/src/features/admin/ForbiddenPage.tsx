import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { SystemScreen } from "@/components/SystemScreen";
import { logout } from "@/features/auth/api";

export function ForbiddenPage() {
  const { t } = useTranslation();
  return (
    <SystemScreen title={t("admin.forbidden.title")} body={t("admin.forbidden.description")}>
      <div className="flex items-center gap-2">
        <Button variant="outline" render={<Link to="/" />}>
          {t("admin.forbidden.backHome")}
        </Button>
        <Button
          variant="ghost"
          onClick={() => {
            void logout();
          }}
        >
          {t("admin.forbidden.signOut")}
        </Button>
      </div>
    </SystemScreen>
  );
}
