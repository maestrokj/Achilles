/** Org maintenance flag, set by the API layer on a 503 MAINTENANCE and cleared
 * by the next successful response — the gate component subscribes. */

let active = false;
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

export function subscribeMaintenance(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function isMaintenanceActive(): boolean {
  return active;
}

export function setMaintenanceActive(next: boolean): void {
  if (active === next) return;
  active = next;
  emit();
}
