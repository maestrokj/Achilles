/** The live turn seam: SSE events → the MessageOverlay the thread renders.
 *
 * assistant-ui's runtime is bypassed — we drive `adapter.run()` straight and
 * fold the yielded snapshots, so every event's effect on the overlay (and the
 * error/abort exits) is asserted without a browser. streamChat is the only
 * mock: everything downstream of it is the code under test. */

import { beforeEach, describe, expect, it, vi } from "vitest";

import { SseRequestError } from "@/lib/sse";
import type { SseEvent } from "@/lib/sse";

import { chatErrorMessage, createChatAdapter } from "../adapter";
import type { MessageOverlay } from "../types";

const streamChat = vi.fn();

vi.mock("../api", () => ({
  streamChat: (
    conversationId: number | null,
    body: unknown,
    signal: AbortSignal,
  ): AsyncGenerator<SseEvent> =>
    streamChat(conversationId, body, signal) as AsyncGenerator<SseEvent>,
}));

/** A finished SSE turn as a generator over pre-baked frames. */
function stream(events: SseEvent[]): () => AsyncGenerator<SseEvent> {
  return async function* () {
    await Promise.resolve();
    for (const event of events) yield event;
  };
}

/** Last element, or a loud failure — keeps the assertions free of `!`. */
function last<T>(items: T[]): T {
  const item = items.at(-1);
  if (item === undefined) throw new Error("expected at least one snapshot");
  return item;
}

interface RunResult {
  content: { type: string; text: string }[];
  metadata?: { custom?: { overlay?: MessageOverlay } };
}

/** Run one user turn and collect every yielded snapshot. */
async function run(
  handle: ReturnType<typeof createChatAdapter>,
  text: string,
  signal: AbortSignal = new AbortController().signal,
): Promise<RunResult[]> {
  const messages = [{ role: "user", content: [{ type: "text", text }] }];
  const snapshots: RunResult[] = [];
  // The adapter's messages type is assistant-ui's; the fields it reads (role +
  // text parts) are all this fixture carries.
  const gen = handle.adapter.run({
    messages,
    abortSignal: signal,
  } as unknown as Parameters<typeof handle.adapter.run>[0]) as AsyncGenerator<RunResult>;
  for await (const snapshot of gen) snapshots.push(snapshot);
  return snapshots;
}

function overlayOf(result: RunResult): MessageOverlay {
  const overlay = result.metadata?.custom?.overlay;
  if (!overlay) throw new Error("snapshot carried no overlay");
  return overlay;
}

beforeEach(() => {
  streamChat.mockReset();
});

describe("createChatAdapter — the live turn", () => {
  it("streams a grounded answer: text grows, citations land, done seals the id", async () => {
    const created = vi.fn();
    const handle = createChatAdapter({
      initialConversationId: null,
      onConversationCreated: created,
    });
    streamChat.mockImplementation(
      stream([
        { name: "conversation", data: { id: 42 } },
        { name: "message", data: { user_message_id: 1, model: "m" } },
        { name: "delta", data: { text: "Per " } },
        { name: "delta", data: { text: "the docs [1]." } },
        {
          name: "citations",
          data: { items: [{ marker: 1, entity_id: 7, source_type: "page", title: "Doc" }] },
        },
        { name: "grounding", data: { mode: "grounded", outcome: "found" } },
        { name: "done", data: { assistant_message_id: 99, tokens_used: 12 } },
      ]),
    );

    const snapshots = await run(handle, "how do we deploy?");

    // Lazy creation fired exactly once with the server id.
    expect(created).toHaveBeenCalledExactlyOnceWith(42);
    // The last snapshot is the settled turn.
    const final = last(snapshots);
    expect(final.content[0].text).toBe("Per the docs [1].");
    const overlay = overlayOf(final);
    expect(overlay.conversationId).toBe(42);
    expect(overlay.assistantMessageId).toBe(99);
    expect(overlay.citations).toHaveLength(1);
    expect(overlay.grounding).toEqual({ mode: "grounded", outcome: "found" });
    expect(overlay.searching).toBeNull();
  });

  it("carries the picked model into the request body; omits it until picked", async () => {
    const handle = createChatAdapter({ initialConversationId: 5, onConversationCreated: vi.fn() });
    streamChat.mockImplementation(stream([{ name: "done", data: { assistant_message_id: 1 } }]));

    await run(handle, "hi");
    // First send: no explicit pick → body carries content only (server applies sticky).
    expect(streamChat.mock.calls[0][0]).toBe(5);
    expect(streamChat.mock.calls[0][1]).toEqual({ content: "hi" });

    handle.setModel("second-chat");
    await run(handle, "again");
    expect(streamChat.mock.calls[1][1]).toEqual({ content: "again", model: "second-chat" });
  });

  it("shows a searching state on tool_round and clears it on grounding", async () => {
    const handle = createChatAdapter({ initialConversationId: 5, onConversationCreated: vi.fn() });
    streamChat.mockImplementation(
      stream([
        { name: "tool_round", data: { tools: ["search_knowledge"] } },
        { name: "delta", data: { text: "ok" } },
        { name: "grounding", data: { mode: "grounded", outcome: "empty" } },
        { name: "done", data: { assistant_message_id: 3 } },
      ]),
    );

    const snapshots = await run(handle, "q");

    // A mid-run snapshot exposed the searching tools…
    const searching = snapshots.map((s) => overlayOf(s).searching);
    expect(searching).toContainEqual(["search_knowledge"]);
    // …and the settled turn cleared them.
    expect(overlayOf(last(snapshots)).searching).toBeNull();
  });

  it("throws the localized reason on an SSE error event", async () => {
    const handle = createChatAdapter({ initialConversationId: 5, onConversationCreated: vi.fn() });
    streamChat.mockImplementation(
      stream([
        { name: "delta", data: { text: "partial" } },
        { name: "error", data: { code: "PROVIDER_UNAVAILABLE", detail: "down" } },
      ]),
    );

    await expect(run(handle, "q")).rejects.toThrow(chatErrorMessage("PROVIDER_UNAVAILABLE"));
  });

  it("throws the localized reason from a pre-stream problem (SseRequestError)", async () => {
    const handle = createChatAdapter({
      initialConversationId: null,
      onConversationCreated: vi.fn(),
    });
    // The non-ok response is raised as the stream opens — before the first frame.
    streamChat.mockImplementation(() => {
      throw new SseRequestError(422, {
        type: "about:blank",
        title: "x",
        status: 422,
        detail: "x",
        code: "MODEL_NOT_ALLOWED",
        request_id: "r",
      });
    });

    await expect(run(handle, "q")).rejects.toThrow(chatErrorMessage("MODEL_NOT_ALLOWED"));
  });

  it("swallows the abort: a user-cancelled turn neither throws nor errors", async () => {
    const handle = createChatAdapter({ initialConversationId: 5, onConversationCreated: vi.fn() });
    const controller = new AbortController();
    streamChat.mockImplementation(() => {
      controller.abort();
      throw new Error("aborted");
    });

    // No rejection — the cancelled turn unwinds quietly.
    await expect(run(handle, "q", controller.signal)).resolves.toBeDefined();
  });

  it("keeps the turn alive on an unmount: swallows the detach and drains the rest", async () => {
    const handle = createChatAdapter({ initialConversationId: 5, onConversationCreated: vi.fn() });
    const controller = new AbortController();
    let pulled = 0;
    let releaseTail: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => (releaseTail = resolve));

    streamChat.mockImplementation(async function* () {
      await Promise.resolve();
      pulled += 1;
      yield { name: "delta", data: { text: "a" } }; // arrives before the unmount
      await gate; // held until the test detaches the thread
      pulled += 1;
      yield { name: "delta", data: { text: "b" } }; // only the background drain sees this
      pulled += 1;
      yield { name: "done", data: { assistant_message_id: 9 } };
    });

    const messages = [{ role: "user", content: [{ type: "text", text: "q" }] }];
    const gen = handle.adapter.run({
      messages,
      abortSignal: controller.signal,
    } as unknown as Parameters<typeof handle.adapter.run>[0]) as AsyncGenerator<RunResult>;

    // First frame drives the UI, then the thread unmounts (assistant-ui aborts
    // with an AbortError carrying detach === true).
    const first = await gen.next();
    expect(first.done).toBe(false);
    const detach = Object.assign(new Error("unmounted"), { name: "AbortError", detach: true });
    controller.abort(detach);
    releaseTail();

    // The run ends without yielding more or throwing — the drain owns the tail.
    await expect(gen.next()).resolves.toEqual({ done: true, value: undefined });
    // …and that drain consumed the stream to completion, so the backend can persist.
    await vi.waitFor(() => {
      expect(pulled).toBe(3);
    });
  });

  it("sends nothing when the last user message has no text", async () => {
    const handle = createChatAdapter({ initialConversationId: 5, onConversationCreated: vi.fn() });

    const snapshots = await run(handle, "");

    expect(streamChat).not.toHaveBeenCalled();
    expect(snapshots).toHaveLength(0);
  });
});
