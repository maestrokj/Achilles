import type { TFunction } from "i18next";

/** User roles, most-privileged first — the display and facet order.
 *  Mirrors backend auth roles (source of truth). */
export const ROLES = ["owner", "admin", "member"] as const;
export type Role = (typeof ROLES)[number];

/** The org owner: sole holder of destructive/config authority. */
export const isOwner = (role?: Role | null): boolean => role === "owner";

/** A plain member: no business in the admin panel, lives in the chat. */
export const isMember = (role?: Role | null): boolean => role === "member";

/** owner + admin — everyone allowed into the admin panel. */
export const canAccessAdmin = (role?: Role | null): boolean => role === "owner" || role === "admin";

/** Human label for a role. Open set: bulk-invite rows carry an unvalidated
 *  string, so an unknown value falls back to "member" rather than a broken key. */
export function roleLabel(role: string, t: TFunction): string {
  if (role === "owner") return t("admin.roles.owner");
  if (role === "admin") return t("admin.roles.admin");
  return t("admin.roles.member");
}
