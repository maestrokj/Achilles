import { toast as sonnerToast, type ExternalToast } from "sonner";

import { CopyButton } from "@/components/ui/copy-button";

/** An error takes longer to read than a confirmation, and the reader often wants to
 * copy it before it goes — twice sonner's 4s default, still short enough to clear
 * itself. Hovering pauses the timer. */
const ERROR_DURATION_MS = 8000;

/** Sonner captures the pointer on every toast to drive its swipe-to-dismiss gesture,
 * so the message text cannot be selected with the mouse. A copy button hands the text
 * over in one click instead. Pointer events over a `<button>` are exempt from the
 * gesture, so the toast still swipes away everywhere else. `copyText` overrides what
 * the button copies (a support payload richer than the visible message). */
function errorToast(
  message: React.ReactNode,
  data?: ExternalToast & { copyText?: string },
): string | number {
  const { copyText, ...rest } = data ?? {};
  const copySource = copyText ?? (typeof message === "string" ? message : null);
  const copyable = copySource !== null && !rest.action;
  return sonnerToast.error(message, {
    duration: ERROR_DURATION_MS,
    ...rest,
    action: copyable ? <CopyButton text={copySource} className="-mr-1 self-start" /> : rest.action,
  });
}

/** The app's toast: sonner's, with errors carrying a copy button. */
export const toast = Object.assign(
  (message: string, data?: ExternalToast) => sonnerToast(message, data),
  sonnerToast,
  { error: errorToast },
);
