import { useEffect, type ReactNode } from "react";

import { useBranding } from "@/features/admin/platform/api";
import { useSession } from "@/features/auth/session-context";
import { resolveLocale } from "@/i18n";
import { setOrgDateTimePrefs, setUserDateTimePrefs } from "@/lib/format";

import { applyFaviconAccent } from "./favicon";
import { SplashScreen } from "./SplashScreen";

/** Ink tokens for text on the accent: a light accent needs dark ink and vice
 * versa — the theme's own --primary-foreground stays tuned for the default accent. */
const INK_ON_LIGHT_ACCENT = "oklch(0.2 0 0)";
const INK_ON_DARK_ACCENT = "oklch(0.98 0 0)";
const LUMINANCE_LIGHT_THRESHOLD = 0.5;

/** WCAG relative luminance of a #rrggbb hex (0 = black, 1 = white). */
function relativeLuminance(hex: string): number {
  const channel = (offset: number) => {
    const value = parseInt(hex.slice(offset, offset + 2), 16) / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  };
  const [r, g, b] = [channel(1), channel(3), channel(5)];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function applyAccent(accent: string): void {
  const root = document.documentElement.style;
  const lightAccent = relativeLuminance(accent) > LUMINANCE_LIGHT_THRESHOLD;
  root.setProperty("--primary", accent);
  root.setProperty("--ring", accent);
  root.setProperty("--primary-foreground", lightAccent ? INK_ON_LIGHT_ACCENT : INK_ON_DARK_ACCENT);
  applyFaviconAccent(accent, lightAccent);
}

/** Wires every display preference along the chain personal → org → browser: the
 * org accent color onto the shadcn tokens, and the timezone / date format / UI
 * language resolved from the signed-in user first, then the org default. Both
 * inputs (session + org branding) are read here so precedence is settled in one
 * place rather than racing across effects.
 *
 * Children wait for the branding read. Formatting helpers pull the prefs from a
 * module-level store rather than context — a screen that painted before the org
 * defaults landed would keep its browser-zone timestamps, with no subscription
 * to bring it back. Gating the first paint is what makes that store safe to read.
 */
export function DisplayPrefs({ children }: { children: ReactNode }) {
  const query = useBranding();
  const session = useSession();

  const branding = query.data;
  const user = session.status === "authenticated" ? session.user : null;

  // Written during render, not from an effect: descendants format timestamps and
  // paint accent-colored controls on this very commit, and effects run only after
  // it — an accent applied later repaints the first frame in the theme's default
  // primary. All three setters are idempotent writes nobody subscribes to, so
  // this stays replay-safe.
  setOrgDateTimePrefs(
    branding ? { timeZone: branding.timezone, dateFormat: branding.date_format } : {},
  );
  // Cleared to {} when anonymous, so a prior user's zone never lingers after logout.
  setUserDateTimePrefs(
    user ? { timeZone: user.timezone ?? undefined, dateFormat: user.date_format ?? undefined } : {},
  );
  if (branding) applyAccent(branding.accent_color);

  useEffect(() => {
    resolveLocale(user?.id ?? null, user?.locale, branding?.locale);
  }, [user, branding]);

  // An unreachable branding endpoint must not hold the app hostage — fall through
  // with empty org defaults (browser zone) rather than spinning forever.
  if (query.isPending) return <SplashScreen />;

  return children;
}
