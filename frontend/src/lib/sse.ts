/** POST + SSE over fetch — ky hooks cannot stream, so the Bearer/refresh dance
 * from api/client.ts is replayed by hand: one shared refresh, one retry. */

import { createParser } from "eventsource-parser";

import { refreshSession } from "@/api/client";
import { responseProblem, type ProblemDetails } from "@/api/problems";
import { API_V1_URL } from "@/constants/api";
import { getAccessToken } from "@/features/auth/session-store";

/** A non-ok response before the stream started — carries the problem+json body.
 * `.message` is diagnostic English for logs only; anything user-facing renders
 * `problemReason(error.problem, error.status)` from api/errors.ts instead. */
export class SseRequestError extends Error {
  readonly status: number;
  readonly problem: ProblemDetails | null;

  constructor(status: number, problem: ProblemDetails | null) {
    super(problem?.detail ?? `SSE request failed with status ${String(status)}`);
    this.name = "SseRequestError";
    this.status = status;
    this.problem = problem;
  }
}

export interface SseEvent {
  name: string;
  data: unknown;
}

async function requestOnce(
  path: string,
  body: unknown,
  signal: AbortSignal,
  allowRefresh: boolean,
  method: "GET" | "POST" = "POST",
): Promise<Response> {
  const headers: Record<string, string> = { Accept: "text/event-stream" };
  if (method === "POST") headers["Content-Type"] = "application/json";
  const token = getAccessToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  const response = await fetch(`${API_V1_URL}/${path}`, {
    method,
    headers,
    credentials: "include",
    body: method === "POST" ? JSON.stringify(body) : undefined,
    signal,
  });
  if (response.ok) return response;
  if (response.status === 401 && allowRefresh && (await refreshSession())) {
    return requestOnce(path, body, signal, false, method);
  }
  throw new SseRequestError(response.status, await responseProblem(response));
}

/** GETs an endless SSE endpoint (the events stream) — Bearer auth, unlike EventSource. */
function getSse(path: string, signal: AbortSignal): AsyncGenerator<SseEvent> {
  return streamSse(path, undefined, signal, "GET");
}

const RECONNECT_MIN_MS = 1_000;
const RECONNECT_MAX_MS = 30_000;
/** Twice the server heartbeat (25s pings): silence beyond this means a dead
 * wire the socket never noticed — abort and reconnect. */
const WATCHDOG_MS = 55_000;

/** Consumes an endless GET stream for the life of `signal`: exponential
 * reconnect backoff (reset on any live frame) plus a dead-wire watchdog.
 * Resolves only when `signal` aborts; stream errors feed the backoff. */
export async function streamWithReconnect(
  path: string,
  signal: AbortSignal,
  onEvent: (event: SseEvent) => void,
): Promise<void> {
  let delay = RECONNECT_MIN_MS;
  while (!signal.aborted) {
    const wire = new AbortController();
    const propagateAbort = () => {
      wire.abort();
    };
    signal.addEventListener("abort", propagateAbort, { once: true });
    let watchdog = setTimeout(() => {
      wire.abort();
    }, WATCHDOG_MS);
    try {
      for await (const event of getSse(path, wire.signal)) {
        delay = RECONNECT_MIN_MS; // a live frame proves the wire works
        clearTimeout(watchdog);
        watchdog = setTimeout(() => {
          wire.abort();
        }, WATCHDOG_MS);
        onEvent(event);
      }
    } catch {
      // fall through to the backoff
    } finally {
      clearTimeout(watchdog);
      signal.removeEventListener("abort", propagateAbort);
    }
    // eslint-disable-next-line @typescript-eslint/no-unnecessary-condition -- abort() flips it from another tick
    if (signal.aborted) return;
    await new Promise((resolve) => setTimeout(resolve, delay));
    delay = Math.min(delay * 2, RECONNECT_MAX_MS);
  }
}

/** POSTs JSON and yields the SSE response event by event.
 * Ends when the stream does; throws on pre-stream errors and aborts. */
export function postSse(
  path: string,
  body: unknown,
  signal: AbortSignal,
): AsyncGenerator<SseEvent> {
  return streamSse(path, body, signal, "POST");
}

async function* streamSse(
  path: string,
  body: unknown,
  signal: AbortSignal,
  method: "GET" | "POST",
): AsyncGenerator<SseEvent> {
  const response = await requestOnce(path, body, signal, true, method);
  if (!response.body) throw new SseRequestError(response.status, null);

  const pending: SseEvent[] = [];
  const parser = createParser({
    onEvent: (message) => {
      if (!message.event) return;
      try {
        pending.push({ name: message.event, data: JSON.parse(message.data) });
      } catch {
        // a malformed frame is dropped, the stream carries on
      }
    },
  });

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) return;
    parser.feed(decoder.decode(value, { stream: true }));
    yield* pending.splice(0);
  }
}
