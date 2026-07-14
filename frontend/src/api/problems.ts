/** RFC 9457 problem+json envelope — mirrors backend/src/achilles/api/problems.py. */

import { HTTPError } from "ky";

/** Every problem code the backend can emit — one home, mirrors the backend
 * constants (api/problems.py + per-module constants.py). Each code has a
 * localized reason under `errors.codes.*` in both locales; a unit test keeps
 * the three lists in sync. */
export const PROBLEM_CODES = {
  ACCOUNT_DEACTIVATED: "ACCOUNT_DEACTIVATED",
  AGENT_BUDGET_EXCEEDED: "AGENT_BUDGET_EXCEEDED",
  AGENT_NOT_RUNNABLE: "AGENT_NOT_RUNNABLE",
  AGENT_RUN_ACTIVE: "AGENT_RUN_ACTIVE",
  ALREADY_LINKED: "ALREADY_LINKED",
  BACKUP_NOT_CONFIGURED: "BACKUP_NOT_CONFIGURED",
  CONFIRM_MISMATCH: "CONFIRM_MISMATCH",
  CONFLICT: "CONFLICT",
  EMAIL_TAKEN: "EMAIL_TAKEN",
  EMBEDDING_DIM_MISMATCH: "EMBEDDING_DIM_MISMATCH",
  EMBEDDINGS_UNAVAILABLE: "EMBEDDINGS_UNAVAILABLE",
  FORBIDDEN: "FORBIDDEN",
  INTERNAL_ERROR: "INTERNAL_ERROR",
  INVALID_CREDENTIALS: "INVALID_CREDENTIALS",
  INVITE_EXPIRED: "INVITE_EXPIRED",
  INVITE_USED: "INVITE_USED",
  LAST_DEFAULT_PROTECTED: "LAST_DEFAULT_PROTECTED",
  LAST_OWNER_PROTECTED: "LAST_OWNER_PROTECTED",
  LINK_EXPIRED: "LINK_EXPIRED",
  MAINTENANCE: "MAINTENANCE",
  MATTERMOST_ENABLE_FAILED: "MATTERMOST_ENABLE_FAILED",
  MCP_DISABLED: "MCP_DISABLED",
  MODEL_IN_USE: "MODEL_IN_USE",
  MODEL_NOT_ALLOWED: "MODEL_NOT_ALLOWED",
  MODEL_TOO_LARGE: "MODEL_TOO_LARGE",
  MODEL_TYPE_MISMATCH: "MODEL_TYPE_MISMATCH",
  NO_CHAT_MODEL: "NO_CHAT_MODEL",
  NOT_FOUND: "NOT_FOUND",
  PASSWORD_CHANGE_REQUIRED: "PASSWORD_CHANGE_REQUIRED",
  PROVIDER_UNAVAILABLE: "PROVIDER_UNAVAILABLE",
  PROVIDER_UNREACHABLE: "PROVIDER_UNREACHABLE",
  RATE_LIMITED: "RATE_LIMITED",
  REEMBED_IN_PROGRESS: "REEMBED_IN_PROGRESS",
  RESET_EXPIRED: "RESET_EXPIRED",
  RUN_ALREADY_ACTIVE: "RUN_ALREADY_ACTIVE",
  RUN_ALREADY_FINISHED: "RUN_ALREADY_FINISHED",
  SESSION_NOT_FOUND: "SESSION_NOT_FOUND",
  SETUP_UNAVAILABLE: "SETUP_UNAVAILABLE",
  SLACK_HOOK_UNAVAILABLE: "SLACK_HOOK_UNAVAILABLE",
  SLACK_SIGNATURE_INVALID: "SLACK_SIGNATURE_INVALID",
  SMTP_NOT_CONFIGURED: "SMTP_NOT_CONFIGURED",
  SYSTEM_PROVIDER_PROTECTED: "SYSTEM_PROVIDER_PROTECTED",
  TELEGRAM_HOOK_UNAVAILABLE: "TELEGRAM_HOOK_UNAVAILABLE",
  TELEGRAM_SECRET_INVALID: "TELEGRAM_SECRET_INVALID",
  TELEGRAM_WEBHOOK_FAILED: "TELEGRAM_WEBHOOK_FAILED",
  TELEGRAM_WEBHOOK_NOT_PUBLIC: "TELEGRAM_WEBHOOK_NOT_PUBLIC",
  TOKEN_EXPIRED: "TOKEN_EXPIRED",
  TOKEN_INVALID: "TOKEN_INVALID",
  UNKNOWN_CONNECTOR: "UNKNOWN_CONNECTOR",
  UNKNOWN_PLACEHOLDER: "UNKNOWN_PLACEHOLDER",
  UNKNOWN_TOOL: "UNKNOWN_TOOL",
  VALIDATION_ERROR: "VALIDATION_ERROR",
  WEBHOOK_NOT_SUPPORTED: "WEBHOOK_NOT_SUPPORTED",
  WEBHOOK_SIGNATURE_INVALID: "WEBHOOK_SIGNATURE_INVALID",
  WEBHOOK_UNAVAILABLE: "WEBHOOK_UNAVAILABLE",
} as const;

export interface ProblemDetails {
  type: string;
  title: string;
  status: number;
  detail: string;
  code: string;
  request_id: string;
  retry_after?: number;
  errors?: { field: string; message: string }[];
}

function isProblem(body: unknown): body is ProblemDetails {
  return typeof body === "object" && body !== null && "code" in body && "status" in body;
}

export async function responseProblem(response: Response): Promise<ProblemDetails | null> {
  try {
    const body: unknown = await response.clone().json();
    if (isProblem(body)) return body;
  } catch {
    // non-JSON body — not a problem document
  }
  return null;
}

/** Extract the problem document from a ky error, if there is one. */
export async function toProblem(error: unknown): Promise<ProblemDetails | null> {
  if (!(error instanceof HTTPError)) return null;
  // ky pre-parses the error body into `error.data` and consumes the raw response
  // stream doing so — `error.response.json()` then fails ("body already used").
  // Read the parsed data; fall back to the response only for callers that never
  // went through ky (there is no live body to lose there).
  if (isProblem(error.data)) return error.data;
  return responseProblem(error.response);
}
