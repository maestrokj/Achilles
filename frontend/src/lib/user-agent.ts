/** Best-effort "Browser on OS" label from a raw User-Agent string.
 * Deliberately small — the sessions screen only needs a recognisable hint,
 * not a full parser. Unknown agents fall back gracefully. */

const BROWSERS: [RegExp, string][] = [
  [/Edg\//, "Edge"],
  [/OPR\/|Opera/, "Opera"],
  [/Firefox\//, "Firefox"],
  [/Chrome\//, "Chrome"],
  [/Safari\//, "Safari"],
];

const SYSTEMS: [RegExp, string][] = [
  [/Windows NT/, "Windows"],
  [/Mac OS X|Macintosh/, "macOS"],
  [/Android/, "Android"],
  [/iPhone|iPad|iOS/, "iOS"],
  [/Linux/, "Linux"],
];

function match(ua: string, table: [RegExp, string][]): string | null {
  for (const [re, name] of table) {
    if (re.test(ua)) return name;
  }
  return null;
}

export interface Device {
  browser: string | null;
  os: string | null;
}

export function parseUserAgent(ua: string | null): Device {
  if (!ua) return { browser: null, os: null };
  return { browser: match(ua, BROWSERS), os: match(ua, SYSTEMS) };
}
