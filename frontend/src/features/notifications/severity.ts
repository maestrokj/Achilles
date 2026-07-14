/** Shared severity → palette mapping — one home for the three-way tone. */

/** The single leading marker of a feed row: one dot carries both meanings.
 * Colour is severity (critical · warning · info-accent); the *state* is read —
 * unread rows show a filled dot, read rows fade to a hollow ring. No second
 * dot on the right; the row gains or loses weight with this one mark. */
export function severityDot(severity: string, read = false): string {
  const base = "mt-1.5 size-1.5 shrink-0 rounded-full";
  if (read) return `${base} ring-1 ring-inset ring-muted-foreground/35`;
  if (severity === "critical") return `${base} bg-destructive`;
  if (severity === "warning") return `${base} bg-warning`;
  return `${base} bg-primary`;
}

/** Border + text classes for a severity pill (matrix rows, chips). */
export function severityTone(severity: string): string {
  if (severity === "critical") return "border-destructive/40 text-destructive";
  if (severity === "warning") return "border-warning/40 text-warning";
  return "border-border text-muted-foreground";
}
