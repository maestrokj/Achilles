import { setupServer } from "msw/node";

/** Absolute-path matcher for handlers: MSW in Node needs an origin wildcard,
 * the ky client itself calls the relative `/api/v1/...`. */
export function apiUrl(path: string): string {
  return `*/api/v1${path}`;
}

export const server = setupServer();
