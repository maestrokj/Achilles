import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import en from "./locales/en";
import ru from "./locales/ru";

const LOCALE_STORAGE_KEY = "achilles.locale";

export type AppLocale = "en" | "ru";

function isAppLocale(value: unknown): value is AppLocale {
  return value === "en" || value === "ru";
}

/** The device-level override: a language picked from the header or the sidebar,
 * remembered per account. `owner` is the user id that made the choice (null when
 * nobody was signed in), so the next person at this browser gets their own
 * language instead of inheriting the previous one. */
interface StoredLocale {
  locale: AppLocale;
  owner: number | null;
}

/** localStorage access throws when the browser blocks site data (strict cookie
 * settings, sandboxed iframes); the preference is a nicety, so swallow it.
 * Reached through `window` — a bare `localStorage` binds to Node's experimental
 * global under the test runner, not to the document's store. */
function readStoredLocale(): StoredLocale | null {
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(LOCALE_STORAGE_KEY);
  } catch {
    return null;
  }
  if (raw === null) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) return null;
    const { locale, owner } = parsed as Record<string, unknown>;
    if (!isAppLocale(locale)) return null;
    return { locale, owner: typeof owner === "number" ? owner : null };
  } catch {
    // Written by an older build as a bare "en" / "ru" — honour it, but treat it
    // as ownerless so the first signed-in resolve hands control back to the chain.
    return isAppLocale(raw) ? { locale: raw, owner: null } : null;
  }
}

function detectLocale(): AppLocale {
  const stored = readStoredLocale();
  if (stored) return stored.locale;
  return navigator.language.toLowerCase().startsWith("ru") ? "ru" : "en";
}

export function currentLocale(): AppLocale {
  return i18n.language === "ru" ? "ru" : "en";
}

/** Pin a language to this device for the signed-in account (or for the anonymous
 * screens when nobody is). Outranks both the personal and the org setting. */
export function setLocale(locale: AppLocale, owner: number | null = null): void {
  const stored: StoredLocale = { locale, owner };
  try {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, JSON.stringify(stored));
  } catch {
    // Blocked storage — the choice just won't persist across reloads.
  }
  void i18n.changeLanguage(locale);
}

/** Drop the device override so the personal → org chain rules again — used when
 * the user clears their personal language back to the org default. */
export function clearStoredLocale(): void {
  try {
    window.localStorage.removeItem(LOCALE_STORAGE_KEY);
  } catch {
    // Blocked storage — nothing was persisted to begin with.
  }
}

/** Resolve the effective UI language along the chain device → personal → org →
 * browser. The device override wins, but only for the account that set it: a
 * stale one is never imposed on anybody else. Signing out merely stops applying
 * it — the override survives, so its owner finds their language again on the way
 * back in; only another account signing in erases it. Unknown codes are ignored —
 * the browser-detected init value stands. Values come from the backend catalog. */
export function resolveLocale(
  userId: number | null,
  personal?: string | null,
  org?: string | null,
): void {
  const stored = readStoredLocale();
  if (stored && stored.owner === userId) {
    // Re-assert it: an anonymous screen in between may have moved the language.
    if (stored.locale !== i18n.language) void i18n.changeLanguage(stored.locale);
    return;
  }
  if (stored && userId !== null) clearStoredLocale();
  const chosen = personal ?? org;
  if (isAppLocale(chosen) && chosen !== i18n.language) {
    void i18n.changeLanguage(chosen);
  }
}

void i18n.use(initReactI18next).init({
  resources: {
    en: { translation: en },
    ru: { translation: ru },
  },
  lng: detectLocale(),
  fallbackLng: "en",
  interpolation: {
    escapeValue: false,
  },
});
