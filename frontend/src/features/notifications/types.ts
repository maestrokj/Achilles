/** Mirrors backend/src/achilles/notifications/schemas.py. */

export const EVENT_TYPE_KEYS = [
  "sync",
  "security",
  "budget",
  "system",
  "discovery",
  "agent",
  "account",
] as const;
export type EventTypeKey = (typeof EVENT_TYPE_KEYS)[number];

/** Broadcast categories only — the org routing matrix rows. Mirrors backend
 * ORG_TYPES (notifications/constants.py); personal types (agent/account) are
 * managed per person in their own profile, never routed over channels. */
export const ORG_TYPE_KEYS = [
  "sync",
  "security",
  "budget",
  "system",
  "discovery",
] as const satisfies readonly EventTypeKey[];

/** Which app surface a notification view renders in: admin panel vs. end-user app. */
export type Surface = "admin" | "app";

/** Notification severity — the catalog's loudness scale. */
export type Severity = "info" | "warning" | "critical";

/** Outbound webhook channel presets (Slack has a bespoke payload; generic is raw). */
export type WebhookPreset = "slack" | "generic";

export interface NotificationItem {
  id: number;
  event: string;
  event_type: EventTypeKey;
  severity: Severity;
  title: string;
  body: string | null;
  source: string | null;
  source_ref: string | null;
  dedup_count: number;
  created_at: string;
  last_seen_at: string | null;
  read_at: string | null;
}

export interface Pref {
  event_type: EventTypeKey;
  in_app_enabled: boolean;
  email_enabled: boolean;
}

export interface Channel {
  id: number;
  kind: "in_app" | "email" | "webhook";
  preset: WebhookPreset | null;
  name: string;
  is_builtin: boolean;
  enabled: boolean;
  url_mask: string | null;
  secret_set: boolean;
  last_test_ok: boolean | null;
  last_test_at: string | null;
}

export interface RouteCell {
  event_type: EventTypeKey;
  channel_id: number;
  enabled: boolean;
  locked: boolean;
  /** The category's loudest catalog event — the matrix row badge (RouteOut.severity). */
  severity: Severity;
}
