/** Locale-aware display helpers — UTC from the wire, user locale on screen. */

/** Intl constructors resolve locale data on every call — cache one instance per locale. */
function perLocale<T>(create: (locale: string) => T): (locale: string) => T {
  const cache = new Map<string, T>();
  return (locale) => {
    let format = cache.get(locale);
    if (format === undefined) {
      format = create(locale);
      cache.set(locale, format);
    }
    return format;
  };
}

/** Rendering prefs for date/time — the chain is personal → org → browser.
 * Org defaults come from platform_settings (/platform/branding); the personal
 * layer from the signed-in user's profile. Both are pushed by DisplayPrefs while
 * it renders, above every consumer, so a timestamp formatted in the same commit
 * already sees them; an unset field falls through to the next link. */
export interface DateTimePrefs {
  timeZone?: string;
  dateFormat?: string;
}

let orgPrefs: DateTimePrefs = {};
let userPrefs: DateTimePrefs = {};

/** Org-level defaults — the middle link of the chain. */
export function setOrgDateTimePrefs(prefs: DateTimePrefs): void {
  orgPrefs = prefs;
}

/** Personal overrides for the signed-in user — the top link; pass `{}` when
 * anonymous so a prior user's zone never lingers after logout. */
export function setUserDateTimePrefs(prefs: DateTimePrefs): void {
  userPrefs = prefs;
}

/** The backend DateFormat catalog (auth/constants.py) mapped to a pinned
 * formatting locale: the locale supplies the field order and separators that
 * make the layout visibly match, NUMERIC_DATE_TIME the digit widths. */
const DATE_FORMAT_LOCALE: Record<string, string> = {
  "DD.MM.YYYY": "de-DE", // 04.07.2026, 14:30
  "MM/DD/YYYY": "en-US", // 07/04/2026, 02:30 PM
  "YYYY-MM-DD": "sv-SE", // 2026-07-04 14:30
};
const NUMERIC_DATE_TIME: Intl.DateTimeFormatOptions = {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
};

/** Prefs are part of the cache key — changing them must not serve stale formatters. */
const dateTimeCache = new Map<string, Intl.DateTimeFormat>();

function dateTimeFormatter(locale: string, dateStyle: "short" | "medium"): Intl.DateTimeFormat {
  const timeZone = userPrefs.timeZone ?? orgPrefs.timeZone;
  const dateFormat = userPrefs.dateFormat ?? orgPrefs.dateFormat;
  const key = `${dateStyle}|${locale}|${timeZone ?? ""}|${dateFormat ?? ""}`;
  let format = dateTimeCache.get(key);
  if (format === undefined) {
    const pinned = dateFormat === undefined ? undefined : DATE_FORMAT_LOCALE[dateFormat];
    format =
      pinned === undefined
        ? new Intl.DateTimeFormat(locale, { dateStyle, timeStyle: "short", timeZone })
        : new Intl.DateTimeFormat(pinned, { ...NUMERIC_DATE_TIME, timeZone });
    dateTimeCache.set(key, format);
  }
  return format;
}

const tokensFormat = perLocale(
  (locale) => new Intl.NumberFormat(locale, { notation: "compact", maximumFractionDigits: 1 }),
);
const weekdayShortFormat = perLocale(
  (locale) => new Intl.DateTimeFormat(locale, { weekday: "short", timeZone: "UTC" }),
);
const weekdayLongFormat = perLocale(
  (locale) => new Intl.DateTimeFormat(locale, { weekday: "long", timeZone: "UTC" }),
);

/** Compact absolute timestamp (short date) — dense tables and run journals. */
export function formatDateTime(iso: string, locale: string): string {
  return dateTimeFormatter(locale, "short").format(new Date(iso));
}

/** Readable absolute timestamp (medium date); null in → null out (the caller words "never"). */
export function formatWhen(iso: string | null, locale: string): string | null {
  if (!iso) return null;
  return dateTimeFormatter(locale, "medium").format(new Date(iso));
}

const numberFormat = perLocale((locale) => new Intl.NumberFormat(locale));

export function formatNumber(value: number, locale: string): string {
  return numberFormat(locale).format(value);
}

const secondsFormat = perLocale(
  (locale) =>
    new Intl.NumberFormat(locale, { style: "unit", unit: "second", unitDisplay: "narrow" }),
);
const minutesFormat = perLocale(
  (locale) =>
    new Intl.NumberFormat(locale, { style: "unit", unit: "minute", unitDisplay: "narrow" }),
);
const hoursFormat = perLocale(
  (locale) => new Intl.NumberFormat(locale, { style: "unit", unit: "hour", unitDisplay: "narrow" }),
);

const SECONDS_PER_MINUTE = 60;
export const MINUTES_PER_HOUR = 60;

/** Compact run duration — "42s", "1m 30s", "1h 12m" (localized narrow units).
 * Truncates rather than rounds, so a short run is never overstated. */
export function formatDuration(seconds: number, locale: string): string {
  const whole = Math.max(0, Math.floor(seconds));
  if (whole < SECONDS_PER_MINUTE) return secondsFormat(locale).format(whole);
  const minutes = Math.floor(whole / SECONDS_PER_MINUTE);
  if (minutes < MINUTES_PER_HOUR) {
    const secs = whole % SECONDS_PER_MINUTE;
    const minutesPart = minutesFormat(locale).format(minutes);
    return secs === 0 ? minutesPart : `${minutesPart} ${secondsFormat(locale).format(secs)}`;
  }
  const hours = Math.floor(minutes / MINUTES_PER_HOUR);
  const rest = minutes % MINUTES_PER_HOUR;
  const hoursPart = hoursFormat(locale).format(hours);
  return rest === 0 ? hoursPart : `${hoursPart} ${minutesFormat(locale).format(rest)}`;
}

/** Run duration from two ISO stamps — null if either end is missing (run still open). */
export function formatDurationBetween(
  startedAt: string | null,
  finishedAt: string | null,
  locale: string,
): string | null {
  if (!startedAt || !finishedAt) return null;
  return formatDuration((Date.parse(finishedAt) - Date.parse(startedAt)) / 1000, locale);
}

export function formatBytes(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024).toString()} kB`;
  return bytes.toString();
}

export function formatTokens(value: number, locale: string): string {
  return tokensFormat(locale).format(value);
}

const moneyFormat = perLocale(
  (locale) => new Intl.NumberFormat(locale, { style: "currency", currency: "USD" }),
);
const priceFormat = perLocale(
  (locale) =>
    new Intl.NumberFormat(locale, { style: "currency", currency: "USD", maximumFractionDigits: 4 }),
);

/** USD spend for screens — the wire carries full-precision Decimal strings.
 * Two decimals; real-but-sub-cent spend reads "<$0.01", never a lying $0.00. */
export function formatMoney(value: string | number, locale: string): string {
  const amount = Number(value);
  if (amount > 0 && amount < 0.01) return `<${moneyFormat(locale).format(0.01)}`;
  return moneyFormat(locale).format(amount);
}

/** Per-1M-token price — up to four decimals so an entered $0.075 shows as set,
 * not rounded into a different price. */
export function formatPrice(value: string | number, locale: string): string {
  return priceFormat(locale).format(Number(value));
}

/** Up to two uppercase initials for a monogram avatar; "·" when the name is blank. */
export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).slice(0, 2);
  const letters = parts.map((part) => part.charAt(0).toUpperCase()).join("");
  return letters || "·";
}

/** Backend weekday: 0 = Monday. 2024-01-01 is a Monday — a stable anchor for Intl. */
export function weekdayShort(weekday: number, locale: string): string {
  return weekdayShortFormat(locale).format(new Date(Date.UTC(2024, 0, 1 + weekday)));
}

export function weekdayLong(weekday: number, locale: string): string {
  return weekdayLongFormat(locale).format(new Date(Date.UTC(2024, 0, 1 + weekday)));
}

/** Backend weekday codes, Monday-first — schedule selects iterate these. */
export const WEEKDAYS = [0, 1, 2, 3, 4, 5, 6] as const;
