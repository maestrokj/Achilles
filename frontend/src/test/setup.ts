import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";

import "../i18n";
import { server } from "./msw";

// This runner hands us a window whose localStorage is an empty object (Node's
// experimental global shadows jsdom's Storage). The theme and the language
// override persist there, so stand up a real in-memory one.
if (typeof window.localStorage.getItem !== "function") {
  const store = new Map<string, string>();
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => store.set(key, value),
      removeItem: (key: string) => store.delete(key),
      clear: () => {
        store.clear();
      },
    },
  });
}

// jsdom has no ResizeObserver; TruncatedText observes its element for overflow.
globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

// jsdom has no matchMedia; ThemeProvider and sonner need it.
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }),
});

beforeAll(() => {
  server.listen({ onUnhandledRequest: "error" });
});

afterEach(() => {
  server.resetHandlers();
  cleanup();
  // The theme and the language override outlive a render; wiped here so no test
  // inherits the storage a neighbour left behind.
  window.localStorage.clear();
});

afterAll(() => {
  server.close();
});
