/** Localized presentation of API failures — the reason layer over problems.ts.
 *
 * The backend's `detail` is English-only prose and is never rendered: the
 * user-facing "why" comes from the problem `code` via the `errors.codes.*`
 * locale table, falling back to a status-tier message, so every error speaks
 * the UI language (personal → org → browser). The raw detail and request id
 * survive only in the error toast's copy payload for support. One deliberate
 * exception lives outside this module: integration cards (Telegram / Slack /
 * Mattermost / SMTP) interpolate provider-returned diagnostics into a
 * localized template — that text is the external system's own verdict, not
 * our backend prose. */

import i18n from "i18next";
import { HTTPError } from "ky";

import { PROBLEM_CODES, toProblem, type ProblemDetails } from "@/api/problems";
import { isOnline } from "@/features/app-shell/online-store";
import { toast } from "@/lib/toast";

type KnownCode = keyof typeof PROBLEM_CODES;

type StatusTier =
  | "unauthorized"
  | "forbidden"
  | "notFound"
  | "conflict"
  | "gone"
  | "validation"
  | "rateLimited"
  | "server"
  | "unknown";

const STATUS_KEYS: Record<number, StatusTier> = {
  401: "unauthorized",
  403: "forbidden",
  404: "notFound",
  409: "conflict",
  410: "gone",
  422: "validation",
  429: "rateLimited",
};

function statusReason(status: number | undefined): string {
  const tier = !status ? "unknown" : status >= 500 ? "server" : (STATUS_KEYS[status] ?? "unknown");
  return i18n.t(`errors.status.${tier}`);
}

/** Localized reason for a bare code (SSE error events, replayed chat overlays);
 * null when the code is unknown — the caller picks its contextual fallback. */
export function codeReason(code: string | undefined, retryAfter?: number): string | null {
  if (!code || !(code in PROBLEM_CODES)) return null;
  if (code === PROBLEM_CODES.RATE_LIMITED && retryAfter) {
    return i18n.t("errors.codes.RATE_LIMITED", { seconds: retryAfter });
  }
  return i18n.t(`errors.codes.${code as KnownCode}`);
}

/** Localized "why" of a problem: mapped code → status tier → generic. */
export function problemReason(problem: ProblemDetails | null, status?: number): string {
  return codeReason(problem?.code, problem?.retry_after) ?? statusReason(problem?.status ?? status);
}

/** Localized "why" of any thrown request error — adds network/offline detection
 * for failures that never reached the server (ky throws TypeError on those). */
export async function apiErrorReason(error: unknown): Promise<string> {
  if (error instanceof HTTPError) {
    return problemReason(await toProblem(error), error.response.status);
  }
  if (!isOnline()) return i18n.t("errors.status.offline");
  if (error instanceof TypeError) return i18n.t("errors.status.network");
  return i18n.t("errors.status.unknown");
}

/** The shared onError of mutations: title = what failed (pre-translated,
 * per call site), description = localized why. The copy button carries the
 * backend detail and request id for support. */
export async function toastApiError(error: unknown, context: string): Promise<void> {
  const problem = error instanceof HTTPError ? await toProblem(error) : null;
  const reason = problem ? problemReason(problem) : await apiErrorReason(error);
  const copyText = [
    `${context} — ${reason}`,
    problem && problem.detail !== reason ? problem.detail : null,
    problem?.request_id ? `request_id: ${problem.request_id}` : null,
  ]
    .filter(Boolean)
    .join("\n");
  toast.error(context, { description: reason, copyText });
}
