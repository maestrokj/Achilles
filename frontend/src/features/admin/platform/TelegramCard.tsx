import { useTranslation } from "react-i18next";
import { toast } from "@/lib/toast";

import { LoaderCircleIcon, SendIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { EmptyState } from "@/components/list-controls/EmptyState";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { toastApiError } from "@/api/errors";
import { PROBLEM_CODES, toProblem } from "@/api/problems";

import {
  getTelegramSettings,
  patchTelegramSettings,
  platformKeys,
  testTelegramConnection,
} from "./api";
import { SectionCard } from "./SectionCard";
import { StatusBadge } from "./StatusBadge";
import { useIntegrationCard } from "./useIntegrationCard";

const TELEGRAM_TEST_ERROR_KEYS = [
  "no_token",
  "network_error",
  "webhook_not_public",
  "webhook_missing",
] as const;

/** The #telegram section of the Platform screen (platform-settings.html#telegram):
 * Slack's twin, trimmed. One write-only bot token, a live "test connection" probe
 * (getMe → the bot's @handle), and the master switch — turning it on registers the
 * webhook at Telegram (Achilles owns the generated secret). Owner edits. */
export function TelegramCard({ readOnly }: { readOnly: boolean }) {
  const { t } = useTranslation();
  const { query, save, test, draft, setDraft, saveDraft } = useIntegrationCard({
    queryKey: platformKeys.telegram,
    get: getTelegramSettings,
    patch: patchTelegramSettings,
    test: testTelegramConnection,
    onSaveError: async (error) => {
      // Enabling can be refused server-side (the webhook couldn't register); the
      // switch was rolled back, so the invalidation pulls fresh state — explain why.
      const problem = await toProblem(error);
      // Style-D exception: Telegram's own verdict is interpolated into the
      // localized template — that diagnostic has no code in the registry.
      if (problem?.code === PROBLEM_CODES.TELEGRAM_WEBHOOK_FAILED)
        toast.error(t("admin.platform.telegram.enableErrors.failed", { error: problem.detail }));
      else void toastApiError(error, t("admin.platform.saveFailed"));
    },
    onTestResult: (result) => {
      if (result.ok) toast.success(t("admin.platform.telegram.testOk"));
      else {
        const raw = result.error;
        const error =
          raw === null
            ? t("admin.platform.telegram.testErrorUnknown")
            : (TELEGRAM_TEST_ERROR_KEYS as readonly string[]).includes(raw)
              ? t(
                  `admin.platform.telegram.testErrors.${raw as (typeof TELEGRAM_TEST_ERROR_KEYS)[number]}`,
                )
              : raw;
        toast.error(t("admin.platform.telegram.testFailed", { error }));
      }
    },
  });

  return (
    <SectionCard
      icon={SendIcon}
      title={t("admin.platform.telegram.title")}
      subtitle={t("admin.platform.telegram.subtitle")}
      aside={
        query.data && (
          <div className="flex items-center gap-3">
            <StatusBadge
              ok={query.data.last_test_ok}
              pending={test.isPending}
              pendingLabel={t("admin.platform.testing")}
              labels={{
                ok: t("admin.platform.telegram.statusOk"),
                failed: t("admin.platform.telegram.statusFailed"),
                untested: t("admin.platform.telegram.neverTested"),
              }}
              handle={query.data.bot_username ? `@${query.data.bot_username}` : undefined}
            />
            <Switch
              checked={query.data.enabled}
              // Enabling reaches Telegram (setWebhook), so it needs a saved token;
              // disabling is always allowed.
              disabled={
                readOnly || save.isPending || (!query.data.enabled && !query.data.bot_token_mask)
              }
              onCheckedChange={(next) => {
                save.mutate({ enabled: next });
              }}
            />
          </div>
        )
      }
    >
      {query.isPending ? (
        <Skeleton className="h-32 w-full" />
      ) : query.isError ? (
        <EmptyState
          variant="error"
          description={t("common.list.errorTitle")}
          onRetry={() => {
            void query.refetch();
          }}
        />
      ) : (
        <>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="telegram-token">{t("admin.platform.telegram.botToken")}</Label>
            <Input
              id="telegram-token"
              className="w-64"
              type="password"
              autoComplete="off"
              value={draft?.bot_token ?? ""}
              placeholder={query.data.bot_token_mask ?? t("admin.platform.telegram.secretNotSet")}
              disabled={readOnly}
              onChange={(e) => {
                setDraft({ ...draft, bot_token: e.target.value });
              }}
            />
          </div>

          {!readOnly && (
            <div className="flex justify-end gap-2">
              <Button
                size="sm"
                variant="outline"
                // The probe validates the *saved* token, so require one and
                // block while an unsaved edit lingers (Save it first).
                disabled={test.isPending || !query.data.bot_token_mask || Boolean(draft?.bot_token)}
                onClick={() => {
                  test.mutate();
                }}
              >
                {test.isPending && <LoaderCircleIcon className="animate-spin" />}
                {t("admin.platform.telegram.test")}
              </Button>
              <Button size="sm" disabled={draft === null || save.isPending} onClick={saveDraft}>
                {t("admin.platform.save")}
              </Button>
            </div>
          )}
        </>
      )}
    </SectionCard>
  );
}
