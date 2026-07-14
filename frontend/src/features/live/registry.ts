/** The board → query-key map behind the events stream (`/events/stream`).
 *
 * A `board` frame invalidates the listed prefixes; TanStack Query refetches
 * only the queries actually mounted, so a broad prefix costs nothing on
 * screens that aren't open. Screens carry no live-update code — a screen is
 * live because its keys are listed here, not because it polls. */

import type { QueryKey } from "@tanstack/react-query";

export type Board = "harvester" | "knowledge" | "agents";

export const LIVE_BOARDS: Record<Board, readonly QueryKey[]> = {
  // Sync-run journal + progress counters: the sources list (health badges),
  // per-source run history and dead letters.
  harvester: [
    ["admin", "harvester", "sources"],
    ["admin", "harvester", "runs"],
    ["admin", "harvester", "dlq"],
  ],
  // Curation runs, re-embed progress, backups — plus the embedder card, whose
  // runtime settles in step with the re-embed run it accompanies.
  knowledge: [
    ["admin", "knowledge", "metrics"],
    ["admin", "knowledge", "curation"],
    ["admin", "knowledge", "backups"],
    ["admin", "ai", "embedder"],
  ],
  // The run journal and every surface that shows a "last run" line.
  agents: [
    ["agents", "runs"],
    ["agents", "admin", "runs"],
    ["agents", "list"],
    ["agents", "admin", "list"],
    ["agents", "agent"],
    ["agents", "admin", "agent"],
  ],
};

export function isBoard(value: unknown): value is Board {
  return typeof value === "string" && value in LIVE_BOARDS;
}
