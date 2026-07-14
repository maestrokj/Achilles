/** Contracts of /admin/users, /invites, /admin/identity-mapping, /api-keys. */

import type { OffsetPage } from "@/api/lists";
import type { Role } from "@/features/auth/roles";

export interface AdminUser {
  id: number;
  email: string;
  full_name: string;
  role: Role;
  status: string;
  must_change_password: boolean;
  timezone: string | null;
  locale: string | null;
  date_format: string | null;
  last_login_at: string | null;
  created_at: string;
}

export interface AdminUserDetail extends AdminUser {
  active_sessions: number;
}

export interface AdminUserPatch {
  email?: string;
  full_name?: string;
  role?: AdminUser["role"];
  status?: string;
}

export type InviteStatus = "pending" | "accepted" | "expired";

export interface Invite {
  id: number;
  email: string;
  role: string;
  status: InviteStatus;
  expires_at: string;
  created_at: string;
}

/** Per-row verdict of POST /invites/bulk — same shape for dry-run and send. */
export type BulkRowStatus = "created" | "conflict" | "invalid" | "duplicate";

export interface BulkRow {
  row: number;
  email: string;
  status: BulkRowStatus;
  message?: string;
  /** The role the invite will carry. */
  role: string;
  /** True when `role` was filled from the default-role selector, not the file. */
  role_from_default: boolean;
}

export interface BulkReport {
  results: BulkRow[];
}

export interface MappingLink {
  principal_id: number;
  source_id: number;
  source_user_id: string;
  email: string | null;
  display_name: string | null;
  pinned: boolean;
}

export interface MappingRow {
  user_id: number;
  full_name: string;
  email: string;
  links: MappingLink[];
}

export interface MappingSource {
  id: number;
  name: string;
  connector_type: string;
}

export interface MappingPage extends OffsetPage<MappingRow> {
  sources: MappingSource[];
}

export interface MappingCandidate {
  id: number;
  source_user_id: string;
  email: string | null;
  display_name: string | null;
  linked_user_id: number | null;
  pinned: boolean;
}
