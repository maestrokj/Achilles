/** Contracts of /admin/knowledge/* (knowledge-store admin routes). */

export interface KnowledgeMetrics {
  entities: number;
  chunks: number;
  edges: number;
  pending_refs: number;
  vector_bytes: number;
}

interface CurationRun {
  id: number;
  trigger: "schedule" | "manual" | "model_change";
  state: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  started_at: string | null;
  finished_at: string | null;
  steps: Record<string, unknown> | null;
  error: string | null;
  created_at: string;
  destructive_open: boolean;
}

export interface CurationStatus {
  active: CurationRun | null;
  reembed: {
    done: number;
    total: number;
    /** Display names of the switch endpoints; null — the model row is gone. */
    from_model?: string | null;
    to_model?: string | null;
  } | null;
  last: CurationRun | null;
  next_scheduled: string | null;
}

export interface BackupSettings {
  destination_url: string | null;
  credential_is_set: boolean;
  frequency: "daily" | "weekly";
  weekday: number | null;
  time: string;
  retention_count: number;
}

export interface BackupSettingsPatchBody {
  destination_url?: string | null;
  credential?: string;
  frequency?: "daily" | "weekly";
  weekday?: number | null;
  time?: string;
  retention_count?: number;
}

export interface BackupSnapshot {
  id: number;
  state: "running" | "succeeded" | "failed";
  started_at: string;
  finished_at: string | null;
  size_bytes: number | null;
  error: string | null;
}
