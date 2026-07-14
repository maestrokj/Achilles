/** Calls to backend conversation routes (query_engine/routes/conversations.py). */

import { api } from "@/api/client";
import type { OffsetPage } from "@/api/lists";
import { postSse, type SseEvent } from "@/lib/sse";

import type {
  ChatModelsResponse,
  ChatRequest,
  Conversation,
  ConversationListItem,
  FeedbackValue,
} from "./types";

/** The sidebar shows one page of freshest dialogues — no pager. */
const HISTORY_PER_PAGE = 50;

export const chatQueryKeys = {
  conversation: (id: number) => ["chat", "conversation", id] as const,
  conversations: ["chat", "conversations"] as const,
  models: ["chat", "models"] as const,
};

export function getConversation(id: number): Promise<Conversation> {
  return api.get(`conversations/${String(id)}`).json<Conversation>();
}

export function listConversations(): Promise<OffsetPage<ConversationListItem>> {
  return api
    .get("conversations", { searchParams: { per_page: HISTORY_PER_PAGE } })
    .json<OffsetPage<ConversationListItem>>();
}

export async function renameConversation(id: number, title: string): Promise<void> {
  await api.patch(`conversations/${String(id)}`, { json: { title } });
}

export async function deleteConversation(id: number): Promise<void> {
  await api.delete(`conversations/${String(id)}`);
}

export function getChatModels(): Promise<ChatModelsResponse> {
  return api.get("chat/models").json<ChatModelsResponse>();
}

export async function setFeedback(messageId: number, value: FeedbackValue): Promise<void> {
  await api.patch(`messages/${String(messageId)}/feedback`, { json: { value } });
}

/** A click on a source card — the second demand signal (retrieval.html#access-signal).
 * Fire-and-forget: a failed signal must never disrupt opening the source. */
export async function postAccess(conversationId: number, entityId: number): Promise<void> {
  await api.post(`conversations/${String(conversationId)}/access`, {
    json: { entity_id: entityId },
  });
}

/** One SSE turn: `null` conversation id lazily creates the dialogue. */
export function streamChat(
  conversationId: number | null,
  body: ChatRequest,
  signal: AbortSignal,
): AsyncGenerator<SseEvent> {
  const path =
    conversationId === null ? "conversations" : `conversations/${String(conversationId)}/messages`;
  return postSse(path, body, signal);
}
