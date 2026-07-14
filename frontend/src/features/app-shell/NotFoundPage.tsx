import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { SystemScreen } from "@/components/SystemScreen";

/** 404 stub of system-screens.html — the catch-all route lands here instead of
 * silently redirecting an unknown path to home. */
export function NotFoundPage() {
  const { t } = useTranslation();
  return (
    <SystemScreen code="404" title={t("app.notFound.title")} body={t("app.notFound.body")}>
      <Button variant="outline" render={<Link to="/" />}>
        {t("app.notFound.home")}
      </Button>
    </SystemScreen>
  );
}
