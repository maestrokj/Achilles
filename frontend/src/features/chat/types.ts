/** Mirrors backend/src/achilles/query_engine/schemas.py (wire contract + SSE vocabulary). */

export interface Citation {
  marker: number;
  entity_id: number;
  chunk_id: number | null;
  title?: string | null;
  url?: string | null;
  source_type: string;
  snippet?: string | null;
}

export interface Grounding {
  mode: "grounded" | "conversational";
  outcome: "found" | "empty" | "acl_hidden" | null;
  hidden_source_type: string | null;
  hidden_author_email: string | null;
}

export type FeedbackValue = 1 | -1 | null;

/** Terminal outcome of an assistant turn; null on the row = completed cleanly. */
type FinishReason = "stopped" | "failed";

interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  model: string | null;
  tokens_used: number | null;
  feedback: FeedbackValue;
  created_at: string;
  citations: Citation[] | null;
  finish: FinishReason | null;
  error_code: string | null;
}

export interface Conversation {
  id: number;
  title: string | null;
  selected_model: string | null;
  created_at: string;
  messages: ChatMessage[];
}

/** Sidebar history row (GET /conversations). */
export interface ConversationListItem {
  id: number;
  title: string | null;
  created_at: string;
  last_activity_at: string;
}

interface ChatModelInfo {
  model_id: string;
  display_name: string;
  is_default: boolean;
}

export interface ChatModelsResponse {
  items: ChatModelInfo[];
  /** The user's personal sticky pick — pre-selects a fresh composer ahead of the
   * admin default. null, or a value absent from `items`, → fall to is_default. */
  selected: string | null;
}

export interface ChatRequest {
  content: string;
  model?: string;
}

// --- SSE `data:` payloads, keyed by event name ---

export interface ConversationEvent {
  id: number;
}

export interface DeltaEvent {
  text: string;
}

export interface ToolRoundEvent {
  tools: string[];
}

export interface CitationsEvent {
  items: Citation[];
}

export interface DoneEvent {
  assistant_message_id: number;
  tokens_used: number | null;
}

export interface ErrorEvent {
  code: string;
  detail: string;
}

/** Our per-message layer on top of assistant-ui: stashed in message
 * metadata.custom by the adapter (live) or the history replay. */
export interface MessageOverlay {
  assistantMessageId: number | null;
  /** Owning conversation — lets a source-card click post the access signal. */
  conversationId: number | null;
  grounding: Grounding | null;
  citations: Citation[];
  feedback: FeedbackValue;
  /** Tool names while the model is searching mid-run; null once settled. */
  searching: string[] | null;
  /** Terminal outcome on a replayed turn; null while live or on a clean answer. */
  finish: FinishReason | null;
  /** The reason behind a replayed failed turn — drives the same notice as live. */
  errorCode: string | null;
}
