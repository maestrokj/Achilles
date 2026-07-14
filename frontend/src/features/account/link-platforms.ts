/** The messenger bots that share the account link-code flow — mirrors the
 * backend's LINK_PLATFORMS (auth/constants.py). One registry drives both the
 * /link/:platform screen and the "Connected accounts" buttons, so adding a
 * surface is a single entry here.
 *
 * Product names aren't translated, so the display name is a literal map; this
 * also keeps the i18n keys static (a computed key blows up the typed-key
 * inference — TS2589). */
export const PLATFORM_NAMES = {
  slack: "Slack",
  telegram: "Telegram",
  mattermost: "Mattermost",
} as const;

export type LinkPlatform = keyof typeof PLATFORM_NAMES;

/** Stable display order for the link buttons. */
export const LINK_PLATFORMS = Object.keys(PLATFORM_NAMES) as LinkPlatform[];

export function toPlatform(value: string | undefined): LinkPlatform | null {
  return value !== undefined && value in PLATFORM_NAMES ? (value as LinkPlatform) : null;
}
