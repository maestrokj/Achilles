import { MoonIcon, SunIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { useSession } from "@/features/auth/session-context";
import { currentLocale, setLocale } from "@/i18n";
import { useTheme } from "@/providers/theme-context";

/** Appbar theme + language switchers, shared by the admin and user shells. */
export function HeaderControls() {
  const { t } = useTranslation();
  const { resolvedTheme, setTheme } = useTheme();
  const session = useSession();

  return (
    <>
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label={t("common.header.toggleTheme")}
        onClick={() => {
          setTheme(resolvedTheme === "dark" ? "light" : "dark");
        }}
      >
        {resolvedTheme === "dark" ? <SunIcon /> : <MoonIcon />}
      </Button>

      <Button
        variant="ghost"
        size="sm"
        aria-label={t("common.header.language")}
        onClick={() => {
          setLocale(currentLocale() === "ru" ? "en" : "ru", session.user?.id ?? null);
        }}
      >
        {currentLocale().toUpperCase()}
      </Button>
    </>
  );
}
