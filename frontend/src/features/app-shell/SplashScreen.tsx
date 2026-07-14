import { useTranslation } from "react-i18next";

import { Spinner } from "@/components/ui/spinner";

/** The full-page wait shown while the app resolves what it must know before the
 * first paint: the session (SessionProvider) and the org display defaults
 * (DisplayPrefs). */
export function SplashScreen() {
  const { t } = useTranslation();
  return (
    <div className="bg-background text-foreground flex min-h-screen flex-col items-center justify-center gap-3">
      <Spinner />
      <p className="text-muted-foreground text-sm">{t("common.loading")}</p>
    </div>
  );
}
