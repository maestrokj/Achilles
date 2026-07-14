/** IANA time zones straight from the engine — no bundled tz database to maintain.
 * The list refreshes with the browser; we store the raw IANA string, display adds
 * the current UTC offset as a muted hint so a user recognizes the zone at a glance.
 *
 * Order follows what timezone pickers converge on: the user's own detected zone is
 * pinned first (the pick 90% of people want), the rest run by UTC offset −12 → +14
 * so the list matches the "what time is it there" mental model rather than the
 * useless alphabetical "Africa/Abidjan" default. */

/** `Intl.supportedValuesOf` is widely shipped but still guard it — a bare browser
 * without it falls back to an empty list, and the field degrades to free text. */
function loadZones(): string[] {
  const supported = Intl as typeof Intl & {
    supportedValuesOf?: (key: "timeZone") => string[];
  };
  if (typeof supported.supportedValuesOf !== "function") return [];
  return supported.supportedValuesOf("timeZone");
}

/** The browser's own guess — the sensible default before a user picks explicitly. */
export function browserTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone;
}

/** Minutes east of UTC, parsed from the short-offset name — the sort key. DST makes
 * this seasonal, which is expected: the list reorders with the actual clock. */
function offsetMinutes(timeZone: string): number {
  const body = shortOffset(timeZone).replace("GMT", ""); // "+3" | "-04:30" | "" at zero
  if (!body) return 0;
  const sign = body.startsWith("-") ? -1 : 1;
  const digits = body.replace(/[+-]/, "").split(":");
  const hours = Number(digits[0]);
  const minutes = digits.length > 1 ? Number(digits[1]) : 0;
  return sign * (hours * 60 + minutes);
}

let cachedZones: readonly string[] | undefined;

export function timeZones(): readonly string[] {
  if (cachedZones === undefined) {
    const detected = browserTimeZone();
    const rest = loadZones()
      .filter((zone) => zone !== detected)
      .sort((a, b) => offsetMinutes(a) - offsetMinutes(b) || a.localeCompare(b));
    // Prepend the detected zone even if the engine omits it from the catalog.
    cachedZones = [detected, ...rest];
  }
  return cachedZones;
}

const shortOffsetCache = new Map<string, string>();

/** Raw engine offset name, e.g. "GMT+3" / "GMT-04:30" / "GMT" at zero. Empty on an
 * unknown zone (typed garbage) so callers simply show nothing. */
function shortOffset(timeZone: string): string {
  let raw = shortOffsetCache.get(timeZone);
  if (raw === undefined) {
    try {
      const parts = new Intl.DateTimeFormat("en-US", {
        timeZone,
        timeZoneName: "shortOffset",
      }).formatToParts();
      raw = parts.find((part) => part.type === "timeZoneName")?.value ?? "";
    } catch {
      raw = "";
    }
    shortOffsetCache.set(timeZone, raw);
  }
  return raw;
}

/** Current UTC offset of a zone as a compact "UTC+3" / "UTC−4:30" display hint. */
export function timeZoneOffset(timeZone: string): string {
  const raw = shortOffset(timeZone);
  if (!raw) return "";
  // "GMT" alone means zero offset; "−" (minus sign) reads cleaner than a hyphen.
  return raw === "GMT" ? "UTC" : raw.replace("GMT", "UTC").replace("-", "−");
}
