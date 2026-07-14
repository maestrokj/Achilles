/** Contracts of /admin/settings and /platform/branding (admin/routes.py). */

type CadenceFrequency = "daily" | "weekly";

export interface PlatformSettings {
  org_name: string;
  org_logo_url: string | null;
  org_description: string | null;
  accent_color: string;
  timezone: string;
  locale: string;
  date_format: string;
  /** Catalogs the selects render from — the backend owns the domain values. */
  locale_choices: string[];
  date_format_choices: string[];
  access_token_ttl: number;
  refresh_token_ttl: number;
  session_absolute_ttl: number;
  maintenance_mode: boolean;
  mcp_enabled: boolean;
  ai_monthly_budget: string | null;
  ai_budget_alert_enabled: boolean;
  chat_weekly_token_budget: number | null;
  agent_weekly_token_budget: number | null;
  sync_interval_minutes: number;
  reconcile_minute_of_week: number;
  watchdog_silence_hours: number;
  curation_frequency: CadenceFrequency;
  curation_weekday: number | null;
  curation_time: string;
  updated_at: string;
  smtp_configured: boolean;
}

export type PlatformSettingsPatch = Partial<
  Omit<
    PlatformSettings,
    "updated_at" | "smtp_configured" | "locale_choices" | "date_format_choices"
  >
>;

export interface Branding {
  org_name: string;
  org_logo_url: string | null;
  accent_color: string;
  timezone: string;
  locale: string;
  date_format: string;
}

/** Contract of /admin/smtp (email/routes/admin.py); the password travels one way. */
export type SmtpSecurity = "none" | "starttls" | "ssl_tls";

export interface SmtpSettings {
  is_enabled: boolean;
  host: string | null;
  port: number | null;
  security: SmtpSecurity;
  username: string | null;
  password_mask: string | null;
  from_address: string | null;
  is_available: boolean;
  last_test_ok: boolean | null;
  last_test_at: string | null;
}

export interface SmtpSettingsPatch {
  host?: string;
  port?: number | null;
  security?: SmtpSecurity;
  username?: string;
  password?: string;
  from_address?: string;
  is_enabled?: boolean;
}

export interface SmtpTestResult {
  ok: boolean;
  error: string | null;
}

/** Contract of /admin/slack (slack/routes/admin.py); secrets travel one way. */
export interface SlackSettings {
  enabled: boolean;
  auto_link_by_email: boolean;
  team: string | null;
  team_name: string | null;
  bot_user_id: string | null;
  bot_token_mask: string | null;
  signing_secret_set: boolean;
  last_test_ok: boolean | null;
  last_test_at: string | null;
}

export interface SlackSettingsPatch {
  bot_token?: string;
  signing_secret?: string;
  enabled?: boolean;
  auto_link_by_email?: boolean;
}

export interface SlackTestResult {
  ok: boolean;
  team: string | null;
  team_name: string | null;
  bot_user_id: string | null;
  error: string | null;
}

/** Contract of /admin/telegram (telegram/routes/admin.py); secrets travel one way.
 * Slack's twin, trimmed: no signing secret (Achilles owns the webhook secret) and
 * no auto-link (Telegram exposes no email). */
export interface TelegramSettings {
  enabled: boolean;
  bot_username: string | null;
  bot_token_mask: string | null;
  webhook_secret_set: boolean;
  last_test_ok: boolean | null;
  last_test_at: string | null;
}

export interface TelegramSettingsPatch {
  bot_token?: string;
  enabled?: boolean;
}

export interface TelegramTestResult {
  ok: boolean;
  bot_username: string | null;
  error: string | null;
}

/** Contract of /admin/mattermost (mattermost/routes/admin.py); secrets travel one way.
 * The server address is a setting (any self-hosted API-v4-compatible installation);
 * there is no webhook at all — a singleton listener dials out over WebSocket, and
 * `listener_connected` is its live word on delivery (null = unknown / not running). */
export interface MattermostSettings {
  enabled: boolean;
  base_url: string | null;
  bot_username: string | null;
  bot_token_mask: string | null;
  listener_connected: boolean | null;
  last_test_ok: boolean | null;
  last_test_at: string | null;
}

export interface MattermostSettingsPatch {
  base_url?: string;
  bot_token?: string;
  enabled?: boolean;
}

export interface MattermostTestResult {
  ok: boolean;
  bot_username: string | null;
  error: string | null;
}
