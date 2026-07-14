/** Browser connectivity flag, driven by the window online/offline events — the
 * OfflineBanner subscribes. Mirrors maintenance-store's external-store shape so
 * it plugs straight into useSyncExternalStore. */

export function subscribeOnline(listener: () => void): () => void {
  window.addEventListener("online", listener);
  window.addEventListener("offline", listener);
  return () => {
    window.removeEventListener("online", listener);
    window.removeEventListener("offline", listener);
  };
}

export function isOnline(): boolean {
  return navigator.onLine;
}
