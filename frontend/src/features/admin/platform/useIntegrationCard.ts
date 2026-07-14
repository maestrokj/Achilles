import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "@/lib/toast";

import { toastApiError } from "@/api/errors";

interface IntegrationCardOptions<TSettings, TPatch, TTest> {
  queryKey: readonly unknown[];
  get: () => Promise<TSettings>;
  patch: (patch: TPatch) => Promise<TSettings>;
  test: () => Promise<TTest>;
  /** Extra work after a successful save (e.g. invalidating a dependent query). */
  onSaved?: () => void;
  /** The save-refusal branch (atomic enable); default is the problem toast. */
  onSaveError?: (error: unknown) => void | Promise<void>;
  /** The card's own verdict toast for a completed probe. */
  onTestResult: (result: TTest) => void;
}

/** The wiring every integration card repeats (SMTP, Slack, Telegram, Mattermost):
 * the settings query, the save mutation writing fresh state back into the cache,
 * the live-probe mutation refreshing the card, and the draft of unsaved edits.
 * The card keeps what genuinely differs — fields, verdict toasts, enable rules. */
export function useIntegrationCard<TSettings, TPatch, TTest>({
  queryKey,
  get,
  patch,
  test: runTest,
  onSaved,
  onSaveError,
  onTestResult,
}: IntegrationCardOptions<TSettings, TPatch, TTest>) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey, queryFn: get });
  const [draft, setDraft] = useState<TPatch | null>(null);

  const save = useMutation({
    mutationFn: patch,
    onSuccess: (fresh) => {
      queryClient.setQueryData(queryKey, fresh);
      onSaved?.();
      toast.success(t("admin.platform.saved"));
    },
    onError: (error) => {
      // A refused save may still have persisted the rest of the patch (the
      // atomic-enable rolls only the switch back) — pull honest state first.
      void queryClient.invalidateQueries({ queryKey });
      if (onSaveError) void onSaveError(error);
      else void toastApiError(error, t("admin.platform.saveFailed"));
    },
  });
  const test = useMutation({
    mutationFn: runTest,
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey });
      onTestResult(result);
    },
    onError: (error) => void toastApiError(error, t("admin.platform.saveFailed")),
  });

  const saveDraft = () => {
    if (!draft) return;
    save.mutate(draft, {
      onSuccess: () => {
        setDraft(null);
      },
    });
  };

  return { query, save, test, draft, setDraft, saveDraft };
}
