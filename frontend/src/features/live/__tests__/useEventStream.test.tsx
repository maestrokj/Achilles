import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import type { SseEvent } from "@/lib/sse";

import { useEventStream } from "../useEventStream";

vi.mock("@/lib/sse", () => ({
  streamWithReconnect: vi.fn(() => new Promise(() => undefined)),
}));

const { streamWithReconnect } = await import("@/lib/sse");

function mount() {
  const queryClient = new QueryClient();
  const invalidate = vi.spyOn(queryClient, "invalidateQueries");
  const setData = vi.spyOn(queryClient, "setQueryData");
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  const view = renderHook(
    () => {
      useEventStream();
    },
    { wrapper },
  );
  const mock = vi.mocked(streamWithReconnect);
  const call = mock.mock.calls.at(-1);
  if (!call) throw new Error("streamWithReconnect was not called");
  const [path, signal, onEvent] = call;
  return { view, invalidate, setData, path, signal, onEvent };
}

function emit(onEvent: (event: SseEvent) => void, name: string, data: unknown) {
  onEvent({ name, data });
}

describe("useEventStream", () => {
  it("opens the one stream against /events/stream", () => {
    const { path } = mount();
    expect(path).toBe("events/stream");
  });

  it("a board frame invalidates that board's registry keys", () => {
    const { invalidate, onEvent } = mount();
    emit(onEvent, "board", { board: "harvester" });
    const keys = invalidate.mock.calls.map(([filters]) => filters?.queryKey);
    expect(keys).toContainEqual(["admin", "harvester", "sources"]);
    expect(keys).toContainEqual(["admin", "harvester", "runs"]);
    expect(keys).toContainEqual(["admin", "harvester", "dlq"]);
    expect(keys).not.toContainEqual(["admin", "knowledge", "curation"]);
  });

  it("hello catches up on every subscribed board", () => {
    const { invalidate, onEvent } = mount();
    emit(onEvent, "hello", { boards: ["agents", "knowledge"] });
    const keys = invalidate.mock.calls.map(([filters]) => filters?.queryKey);
    expect(keys).toContainEqual(["agents", "runs"]);
    expect(keys).toContainEqual(["admin", "knowledge", "curation"]);
    expect(keys).not.toContainEqual(["admin", "harvester", "sources"]);
  });

  it("an unread frame feeds the bell counter and the feed", () => {
    const { invalidate, setData, onEvent } = mount();
    emit(onEvent, "unread", { count: 4 });
    expect(setData).toHaveBeenCalledWith(["notifications", "unread"], { count: 4 });
    const keys = invalidate.mock.calls.map(([filters]) => filters?.queryKey);
    expect(keys).toContainEqual(["notifications", "feed"]);
  });

  it("an unknown board is ignored, not crashed on", () => {
    const { invalidate, onEvent } = mount();
    emit(onEvent, "board", { board: "nonsense" });
    emit(onEvent, "board", {});
    emit(onEvent, "ping", {});
    expect(invalidate).not.toHaveBeenCalled();
  });

  it("unmount aborts the stream", () => {
    const { view, signal } = mount();
    expect(signal.aborted).toBe(false);
    view.unmount();
    expect(signal.aborted).toBe(true);
  });
});
