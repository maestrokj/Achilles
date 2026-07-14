import type { SourceHealth, SourceState } from "./types";

export function stateBadgeVariant(state: SourceState): "success" | "warning" | "destructive" {
  if (state === "active") return "success";
  if (state === "paused") return "warning";
  return "destructive";
}

export function healthBadgeVariant(health: SourceHealth): "secondary" | "outline" | "destructive" {
  if (health === "syncing" || health === "queued") return "secondary";
  if (health === "idle") return "outline";
  return "destructive";
}
