/** Deep links from `source_ref` ("source/42", "agent/7", "ai-usage") to the
 * screen where the event gets fixed — wireframe notification-feed.html, legend 8.
 * The vocabulary is the backend's (notifications.models: deep link refs); an
 * unknown ref simply renders as plain text.
 */

import type { Surface } from "./types";

/** Refs whose targets live in the Admin Panel — visible in the admin feed only. */
const ADMIN_TARGETS: Record<string, ((id: string) => string) | undefined> = {
  source: (id) => `/admin/harvester/sources/${id}`,
  user: (id) => `/admin/users/${id}`,
  provider: () => "/admin/ai-models",
  agent: (id) => `/admin/agents/${id}`,
  curation: () => "/admin/knowledge-store",
  backup: () => "/admin/knowledge-store",
  "api-key": () => "/admin/api-keys",
  "ai-usage": () => "/admin/ai-usage",
};

/** Personal-surface targets: an employee's own agents and account. */
const APP_TARGETS: Record<string, ((id: string) => string) | undefined> = {
  agent: (id) => `/agents/${id}`,
  "api-key": () => "/account",
};

export function sourceRefPath(ref: string | null, surface: Surface): string | null {
  if (!ref) return null;
  const [kind = "", id = ""] = ref.split("/", 2);
  const target = (surface === "admin" ? ADMIN_TARGETS : APP_TARGETS)[kind];
  return target ? target(id) : null;
}
