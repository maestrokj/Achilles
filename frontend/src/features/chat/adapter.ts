/** ChatModelAdapter bridging assistant-ui's LocalRuntime to the backend SSE
 * turn. Deltas grow the text part; conversation/grounding/citations/done ride
 * into message metadata.custom as a MessageOverlay for our own layer. */

import type { ChatModelAdapter, ChatModelRunResult } from "@assistant-ui/react";
import i18n from "i18next";

import { codeReason } from "@/api/errors";
import { SseRequestError, type SseEvent } from "@/lib/sse";

import { streamChat } from "./api";
import type {
  ChatRequest,
  CitationsEvent,
  ConversationEvent,
  DeltaEvent,
  DoneEvent,
  ErrorEvent,
  Grounding,
  MessageOverlay,
  ToolRoundEvent,
} from "./types";

export interface ChatAdapterOptions {
  /** The conversation the thread belongs to at mount; null = lazily created. */
  initialConversationId: number | null;
  /** Fired once when the backend lazily creates the conversation. */
  onConversationCreated: (id: number) => void;
}

export interface ChatAdapterHandle {
  adapter: ChatModelAdapter;
  /** The user's explicit pick — rides with every following turn (server keeps it sticky). */
  setModel: (modelId: string) => void;
}

/** Localized reason from the central code registry; chat's own generic when
 * the failure carries no known code. */
export function chatErrorMessage(code: string | undefined): string {
  return codeReason(code) ?? i18n.t("chat.errors.generic");
}

function lastUserText(messages: ChatModelRunOptionsMessages): string {
  const last = messages.at(-1);
  if (!last || last.role !== "user") return "";
  return last.content
    .map((part) => (part.type === "text" ? part.text : ""))
    .filter(Boolean)
    .join("\n");
}

type ChatModelRunOptionsMessages = Parameters<ChatModelAdapter["run"]>[0]["messages"];

/** assistant-ui aborts the run's signal for two very different reasons: the user
 * pressing Stop (`AbortError.detach === false`) and the thread simply unmounting
 * on a route change (`detach === true`). Only the first should cut the request —
 * navigating away must leave the turn running so its answer finishes and lands. */
function isUnmountAbort(signal: AbortSignal): boolean {
  const reason: unknown = signal.reason;
  return (
    reason instanceof Error &&
    reason.name === "AbortError" &&
    (reason as { detach?: unknown }).detach === true
  );
}

/** After the thread unmounts we keep pulling the stream to its end on a detached
 * task: consuming the frames is what holds the socket open, so the backend runs
 * the turn to completion and persists the answer — ready on the user's return. */
async function drainQuietly(iterator: AsyncIterator<SseEvent>): Promise<void> {
  try {
    for (;;) {
      const step = await iterator.next();
      if (step.done) return;
    }
  } catch {
    // The wire died (tab closed, network drop) — the backend salvages its own
    // partial text; a detached drain has no UI left to surface anything to.
  } finally {
    void iterator.return?.();
  }
}

export function createChatAdapter(options: ChatAdapterOptions): ChatAdapterHandle {
  // Send-time cells owned by the adapter: mutated by SSE events and the model
  // picker, read only when a turn starts — never during React render.
  const cells = {
    conversationId: options.initialConversationId,
    model: null as string | null,
  };

  const adapter: ChatModelAdapter = {
    async *run({ messages, abortSignal }) {
      const content = lastUserText(messages);
      if (!content) return;

      const body: ChatRequest = { content };
      if (cells.model !== null) body.model = cells.model;

      // The request rides its own controller: the runtime's abort cuts it only
      // on an explicit Stop, never on an unmount — see isUnmountAbort.
      const wire = new AbortController();
      const onAbort = () => {
        if (!isUnmountAbort(abortSignal)) wire.abort(abortSignal.reason);
      };
      if (abortSignal.aborted) onAbort();
      else abortSignal.addEventListener("abort", onAbort, { once: true });

      let text = "";
      const overlay: MessageOverlay = {
        assistantMessageId: null,
        conversationId: cells.conversationId,
        grounding: null,
        citations: [],
        feedback: null,
        searching: null,
        // Live failures surface through the thrown error (ErrorPrimitive), not
        // the overlay — these only carry a replayed turn's terminal outcome.
        finish: null,
        errorCode: null,
      };
      // A box, not a bare `let`: TS cannot track a closure's mutation of a local,
      // so the terminal check below would narrow a plain variable to `never`.
      const errorBox: { event: ErrorEvent | null } = { event: null };

      const snapshot = (): ChatModelRunResult => ({
        content: [{ type: "text", text }],
        metadata: { custom: { overlay: { ...overlay } } },
      });

      // Folds one event into the turn state; true means the UI wants a repaint.
      const apply = (event: SseEvent): boolean => {
        switch (event.name) {
          case "conversation": {
            const id = (event.data as ConversationEvent).id;
            cells.conversationId = id;
            overlay.conversationId = id;
            options.onConversationCreated(id);
            return false;
          }
          case "delta":
            text += (event.data as DeltaEvent).text;
            // First answer token means the search is behind us — drop the
            // "Searching…" line now instead of letting it linger until `done`,
            // where it would sit beside the reply already streaming in.
            overlay.searching = null;
            return true;
          case "tool_round":
            overlay.searching = (event.data as ToolRoundEvent).tools;
            return true;
          case "grounding":
            overlay.grounding = event.data as Grounding;
            overlay.searching = null;
            return true;
          case "citations":
            overlay.citations = (event.data as CitationsEvent).items;
            return true;
          case "done":
            overlay.assistantMessageId = (event.data as DoneEvent).assistant_message_id;
            overlay.searching = null;
            return true;
          case "error":
            errorBox.event = event.data as ErrorEvent;
            return false;
          default:
            return false; // "message" and unknown events need no UI reaction
        }
      };

      // Manual iteration (not `for await`): leaving the loop must not auto-close
      // the stream, so an unmount can hand the still-open socket to the drain.
      let iterator: AsyncIterator<SseEvent> | null = null;
      let handedOff = false;
      try {
        iterator = streamChat(cells.conversationId, body, wire.signal)[Symbol.asyncIterator]();
        for (;;) {
          const step = await iterator.next();
          if (step.done) break;
          // Navigated away: this mount is gone, but the turn must not be. Hand
          // the live socket to a background drain and stop driving a dead UI.
          if (isUnmountAbort(abortSignal)) {
            handedOff = true;
            void drainQuietly(iterator);
            return;
          }
          if (apply(step.value)) yield snapshot();
        }
      } catch (failure) {
        if (wire.signal.aborted) return; // user pressed Stop — the runtime handles it
        if (failure instanceof SseRequestError) {
          throw new Error(chatErrorMessage(failure.problem?.code));
        }
        throw new Error(chatErrorMessage(undefined));
      } finally {
        if (!handedOff) void iterator?.return?.();
      }

      if (errorBox.event) throw new Error(chatErrorMessage(errorBox.event.code));
    },
  };

  return {
    adapter,
    setModel: (modelId) => {
      cells.model = modelId;
    },
  };
}
