import { afterEach, describe, expect, it, vi } from "vitest";

import { streamWithReconnect } from "../sse";

/** A 200 whose body never produces a frame; aborting the request errors the
 * reader, the way real fetch behaves. */
function silentResponse(signal: AbortSignal | undefined): Response {
  const body = new ReadableStream({
    start(controller) {
      signal?.addEventListener("abort", () => {
        controller.error(new DOMException("Aborted", "AbortError"));
      });
    },
  });
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

describe("streamWithReconnect", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("reconnects with exponential backoff while the wire is down", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn(() => Promise.reject(new Error("down")));
    vi.stubGlobal("fetch", fetchMock);

    const controller = new AbortController();
    const done = streamWithReconnect("events/stream", controller.signal, () => undefined);

    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1_000); // first retry
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1_000); // doubled delay: not yet
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1_000);
    expect(fetchMock).toHaveBeenCalledTimes(3);

    controller.abort();
    await vi.advanceTimersByTimeAsync(60_000);
    await done; // resolves once the signal is aborted
  });

  it("watchdogs a silently dead wire and dials again", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) =>
      Promise.resolve(silentResponse(init?.signal ?? undefined)),
    );
    vi.stubGlobal("fetch", fetchMock);

    const controller = new AbortController();
    const done = streamWithReconnect("events/stream", controller.signal, () => undefined);

    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    // No frame for over twice the heartbeat → abort + one backoff step.
    await vi.advanceTimersByTimeAsync(55_000 + 1_000);
    expect(fetchMock).toHaveBeenCalledTimes(2);

    controller.abort();
    await vi.advanceTimersByTimeAsync(60_000);
    await done;
  });
});
