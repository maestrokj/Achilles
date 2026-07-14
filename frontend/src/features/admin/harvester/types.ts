/** Contracts of /sources/* (harvester routes). */

export type SourceState = "active" | "paused" | "disconnected";
export type SourceHealth = "idle" | "queued" | "syncing" | "error";

/** SourceOut.last_run — the freshest sync_runs row: active if any, else last terminal. */
export interface SourceLastRun {
  state: string;
  mode: string;
  duration_seconds: number | null;
  error: string | null;
  progress_done: number | null;
  progress_total: number | null;
}

export interface Source {
  id: number;
  name: string;
  connector_type: string;
  state: SourceState;
  health: SourceHealth;
  base_url: string | null;
  auth_account: "service" | "personal";
  credential_is_set: boolean;
  scope_mode: "all" | "selected";
  scope_list: string[];
  content_filters: Record<string, boolean>;
  sync_interval: number | null;
  reconcile_interval: number | null;
  reconcile_window: number | null;
  authority_tier: "high" | "normal" | "low";
  incremental_cursor: Record<string, unknown> | null;
  last_probe_at: string | null;
  last_probe_status: string | null;
  last_sync_at: string | null;
  last_run: SourceLastRun | null;
  dlq_count: number;
  entity_count: number;
  webhook_supported: boolean;
  webhook_enabled: boolean;
  webhook_secret_set: boolean;
  webhook_endpoint_url: string | null;
  created_at: string;
}

export interface ConnectorType {
  type: string;
  title: string;
  needs_base_url: boolean;
  credential_label: string;
  scope_kinds: string[];
  collection_toggles: string[];
  webhooks: boolean;
}

export interface WebhookSecret {
  secret: string;
}

export interface DiagnosisStep {
  name: "reachability" | "credentials" | "permissions";
  ok: boolean;
  detail: string;
}

export interface Diagnosis {
  ok: boolean;
  steps: DiagnosisStep[];
}

export interface CatalogItem {
  native_id: string;
  name: string;
  kind: string;
}

export interface SyncRun {
  id: number;
  mode: string;
  trigger: string;
  state: string;
  entities_done: number | null;
  entities_total: number | null;
  error_count: number;
  error_detail: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

export interface DeadLetter {
  id: number;
  source_type: string;
  source_entity_id: string;
  reason: string;
  error_detail: string | null;
  attempts: number;
  updated_at: string;
}

export interface SourceCreateBody {
  name: string;
  connector_type: string;
  base_url?: string | null;
  credential?: string | null;
  auth_account?: string;
  scope_mode?: "all" | "selected";
  scope_list?: string[];
  content_filters?: Record<string, boolean>;
}

export interface SourcePatchBody {
  name?: string;
  base_url?: string | null;
  credential?: string;
  state?: "active" | "paused";
  scope_mode?: "all" | "selected";
  scope_list?: string[];
  content_filters?: Record<string, boolean>;
  sync_interval?: number | null;
  reconcile_interval?: number | null;
  reconcile_window?: number | null;
  authority_tier?: string;
  webhook_enabled?: boolean;
}
