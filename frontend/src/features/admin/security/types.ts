/** Contracts of /admin/api-keys and /admin/audit-log (security screens). */

import type { OffsetPage } from "@/api/lists";
import type { ApiKey } from "@/features/auth/api-keys";

/** API-key lifecycle states — the status facet order. */
export const API_KEY_STATUSES = ["active", "expired", "revoked"] as const;

export interface AdminApiKey extends ApiKey {
  owner: { id: number; full_name: string; email: string };
  /** Computed server-side — the backend clock decides expiry, not the browser. */
  status: (typeof API_KEY_STATUSES)[number];
}

/** Audit page envelope carries the facet catalog — the backend owns the group values. */
export interface AuditPage extends OffsetPage<AuditEntry> {
  groups: string[];
}

export interface AuditEntry {
  id: number;
  actor_id: number | null;
  actor_email: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  result: string;
  ip: string | null;
  user_agent: string | null;
  /** Free-form event payload (AuditLogOut.meta) — rendered verbatim in the expanded row. */
  meta: Record<string, unknown> | null;
  created_at: string;
}
