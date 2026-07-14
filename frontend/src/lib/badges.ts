import type { i18n as I18n, ParseKeys, TFunction } from "i18next";

import type { StatusTone } from "@/components/StatusLine";

/** Badge tone for a run/snapshot state (sync runs, curation, backups). */
export function runStateBadgeVariant(state: string): "secondary" | "destructive" | "outline" {
  if (state === "succeeded") return "secondary";
  if (state === "failed") return "destructive";
  return "outline";
}

/** Badge tone for a user account status: active reads calm, everything else warns. */
export function userStatusBadgeVariant(status: string): "success" | "warning" {
  return status === "active" ? "success" : "warning";
}

/** StatusLine tone for a run/snapshot state — the shared half of the sync,
 * curation and backup status visuals (icons stay with each call site). */
export function runStateTone(state: string): StatusTone {
  if (state === "succeeded") return "success";
  if (state === "failed") return "destructive";
  if (state === "cancelled") return "muted";
  return "primary";
}

const RUN_STATE_KEYS = ["queued", "running", "succeeded", "failed", "stale", "cancelled"] as const;

/** Human label for a run/snapshot state; unknown values fall back to the raw token. */
export function runStateLabel(state: string, t: TFunction): string {
  return (RUN_STATE_KEYS as readonly string[]).includes(state)
    ? t(`common.runStates.${state as (typeof RUN_STATE_KEYS)[number]}`)
    : state;
}

/** Human label for an audit action code ("auth.login"). The backend owns the
 * catalogue, so the set is open: a code without a locale entry renders as its
 * raw slug rather than a broken key — same contract as the audit groups facet. */
export function auditActionLabel(action: string, t: TFunction, i18n: I18n): string {
  const key = `admin.audit.actions.${action}`;
  return i18n.exists(key) ? t(key as ParseKeys) : action;
}
