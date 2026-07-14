/** Contract of GET /admin/dashboard (admin/dashboard.py). */

export interface AttentionItem {
  severity: "critical" | "warning";
  kind: "source_failing" | "dlq" | "backup_failed" | "provider_error" | "budget";
  subject: string | null;
  count: number | null;
  /** Set for per-source kinds; null for aggregate signals. */
  source_id: number | null;
}

export interface Dashboard {
  org_name: string;
  timezone: string;
  is_empty: boolean;
  users: { total: number; pending_invites: number; deactivated: number };
  sources: {
    total: number;
    active: number;
    paused: number;
    disconnected: number;
    failing: number;
  };
  knowledge: { entities: number; chunks: number; edges: number };
  agents: { total: number; active: number; paused: number; failing: number };
  spend: { month_cost: string | null; budget: string | null; alert_enabled: boolean };
  last_sync: {
    state: string;
    started_at: string | null;
    entities: number | null;
    running: number;
  } | null;
  curation: {
    state: string;
    trigger: string;
    reembed_done: number | null;
    reembed_total: number | null;
  } | null;
  last_backup: {
    state: string;
    started_at: string;
    size_bytes: number | null;
  } | null;
  audit:
    | { action: string; actor_email: string | null; success: boolean; created_at: string }[]
    | null;
  attention: AttentionItem[];
  tasks: { pending_invites: number; unmatched_identities: number };
  /** Configured-or-not facts behind the first-run setup card; the sources step
   * is derived from `sources.total` on this side. */
  setup: {
    email: boolean;
    surfaces: boolean;
    embedding: boolean;
    chat_models: boolean;
    agent_models: boolean;
  };
}
