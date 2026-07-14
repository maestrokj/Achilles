import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router-dom";

import "./i18n";
import "./index.css";
import { Toaster } from "./components/ui/sonner";
import { DisplayPrefs } from "./features/app-shell/DisplayPrefs";
import { ErrorBoundary } from "./features/app-shell/ErrorBoundary";
import { MaintenanceGate } from "./features/app-shell/MaintenancePage";
import { OfflineBanner } from "./features/app-shell/OfflineBanner";
import { SessionProvider } from "./features/auth/SessionProvider";
import { QueryProvider } from "./providers/query";
import { ThemeProvider } from "./providers/theme";
import { router } from "./router";

/** A redeploy replaces the hashed chunks, so a tab opened before it fails to
 * lazy-load the next route ("Failed to fetch dynamically imported module").
 * One reload picks up the fresh index.html; the time guard keeps a genuinely
 * broken deploy from reload-looping — the second failure within the window
 * falls through to the error boundary. */
const CHUNK_RELOAD_AT_KEY = "achilles.chunk-reload-at";
const CHUNK_RELOAD_LOOP_MS = 10_000;
window.addEventListener("vite:preloadError", (event) => {
  const last = Number(window.sessionStorage.getItem(CHUNK_RELOAD_AT_KEY));
  if (Date.now() - last < CHUNK_RELOAD_LOOP_MS) return;
  window.sessionStorage.setItem(CHUNK_RELOAD_AT_KEY, String(Date.now()));
  event.preventDefault();
  window.location.reload();
});

const root = document.getElementById("root");
if (!root) throw new Error("Missing #root element");

createRoot(root).render(
  <StrictMode>
    <ErrorBoundary>
      <QueryProvider>
        <ThemeProvider>
          <SessionProvider>
            <OfflineBanner />
            <DisplayPrefs>
              <MaintenanceGate>
                <RouterProvider router={router} />
              </MaintenanceGate>
            </DisplayPrefs>
            <Toaster />
          </SessionProvider>
        </ThemeProvider>
      </QueryProvider>
    </ErrorBoundary>
  </StrictMode>,
);
