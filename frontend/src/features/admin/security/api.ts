/** Calls of the security screens (company API keys, audit log). */

import { api } from "@/api/client";
import { qs, type ListQuery, type OffsetPage } from "@/api/lists";

import type { AdminApiKey, AuditPage } from "./types";

export const securityKeys = {
  companyKeys: (query: ListQuery) => ["admin", "api-keys", query] as const,
  audit: (query: ListQuery) => ["admin", "audit-log", query] as const,
};

export function listCompanyKeys(searchParams: ListQuery): Promise<OffsetPage<AdminApiKey>> {
  return api
    .get("admin/api-keys", { searchParams: qs(searchParams) })
    .json<OffsetPage<AdminApiKey>>();
}

export function listAudit(searchParams: ListQuery): Promise<AuditPage> {
  return api.get("admin/audit-log", { searchParams: qs(searchParams) }).json<AuditPage>();
}
