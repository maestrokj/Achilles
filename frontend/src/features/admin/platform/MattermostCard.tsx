import { useTranslation } from "react-i18next";
import { toast } from "@/lib/toast";

import { LoaderCircleIcon, MessagesSquareIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { EmptyState } from "@/components/list-controls/EmptyState";
import { InfoHint } from "@/components/InfoHint";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { toastApiError } from "@/api/errors";
import { PROBLEM_CODES, toProblem } from "@/api/problems";

import {
  getMattermostSettings,
  patchMattermostSettings,
  platformKeys,
  testMattermostConnection,
} from "./api";
import { SectionCard } from "./SectionCard";
import { StatusBadge } from "./StatusBadge";
import { useIntegrationCard } from "./useIntegrationCard";

const MATTERMOST_TEST_ERROR_KEYS = ["no_token", "no_base_url", "network_error"] as const;

/** The #mattermost section of the Platform screen (platform-settings.html#mattermost):
 * the Telegram twin for a self-hosted server. The server address is a setting (any
 * API-v4-compatible installation, private LAN addresses included), one write-only
 * bot token, a live "test connection" probe (/users/me → the bot's @handle), and
 * the master switch — turning it on live-proves the token. Delivery is a dial-out
 * WebSocket listener; the badge line shows its word. Owner edits. */
export function MattermostCard({ readOnly }: { readOnly: boolean }) {
  const { t } = useTranslation();
  const { query, save, test, draft, setDraft, saveDraft } = useIntegrationCard({
    queryKey: platformKeys.mattermost,
    get: getMattermostSettings,
    patch: patchMattermostSettings,
    test: testMattermostConnection,
    onSaveError: async (error) => {
      // Enabling can be refused server-side (the token probe failed); the switch
      // was rolled back, so the invalidation pulls fresh state — explain why.
      const problem = await toProblem(error);
      // Style-D exception: the Mattermost server's own verdict is interpolated
      // into the localized template.
      if (problem?.code === PROBLEM_CODES.MATTERMOST_ENABLE_FAILED)
        toast.error(t("admin.platform.mattermost.enableErrors.failed", { error: problem.detail }));
      else void toastApiError(error, t("admin.platform.saveFailed"));
    },
    onTestResult: (result) => {
      if (result.ok) toast.success(t("admin.platform.mattermost.testOk"));
      else {
        const raw = result.error;
        const error =
          raw === null
            ? t("admin.platform.mattermost.testErrorUnknown")
            : (MATTERMOST_TEST_ERROR_KEYS as readonly string[]).includes(raw)
              ? t(
                  `admin.platform.mattermost.testErrors.${raw as (typeof MATTERMOST_TEST_ERROR_KEYS)[number]}`,
                )
              : raw;
        toast.error(t("admin.platform.mattermost.testFailed", { error }));
      }
    },
  });

  return (
    <SectionCard
      icon={MessagesSquareIcon}
      title={t("admin.platform.mattermost.title")}
      subtitle={t("admin.platform.mattermost.subtitle")}
      aside={
        query.data && (
          <div className="flex items-center gap-3">
            <StatusBadge
              ok={query.data.last_test_ok}
              pending={test.isPending}
              pendingLabel={t("admin.platform.testing")}
              labels={{
                ok: t("admin.platform.mattermost.statusOk"),
                failed: t("admin.platform.mattermost.statusFailed"),
                untested: t("admin.platform.mattermost.neverTested"),
              }}
              handle={query.data.bot_username ? `@${query.data.bot_username}` : undefined}
            />
            <Switch
              checked={query.data.enabled}
              // Enabling live-proves the token against the server, so it needs a
              // saved address and token; disabling is always allowed.
              disabled={
                readOnly ||
                save.isPending ||
                (!query.data.enabled && (!query.data.bot_token_mask || !query.data.base_url))
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
          <div className="flex flex-wrap items-end gap-6">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="mattermost-url" className="flex items-center gap-1.5">
                {t("admin.platform.mattermost.baseUrl")}
                <InfoHint text={t("admin.platform.mattermost.baseUrlHint")} />
              </Label>
              <Input
                id="mattermost-url"
                className="w-72"
                autoComplete="off"
                placeholder="https://mattermost.company.com"
                value={draft?.base_url ?? query.data.base_url ?? ""}
                disabled={readOnly}
                onChange={(e) => {
                  setDraft({ ...draft, base_url: e.target.value });
                }}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="mattermost-token">{t("admin.platform.mattermost.botToken")}</Label>
              <Input
                id="mattermost-token"
                className="w-64"
                type="password"
                autoComplete="off"
                value={draft?.bot_token ?? ""}
                placeholder={
                  query.data.bot_token_mask ?? t("admin.platform.mattermost.secretNotSet")
                }
                disabled={readOnly}
                onChange={(e) => {
                  setDraft({ ...draft, bot_token: e.target.value });
                }}
              />
            </div>
          </div>

          {query.data.enabled && (
            <p className="text-muted-foreground text-sm">
              {query.data.listener_connected
                ? t("admin.platform.mattermost.listening")
                : t("admin.platform.mattermost.notListening")}
            </p>
          )}

          {!readOnly && (
            <div className="flex justify-end gap-2">
              <Button
                size="sm"
                variant="outline"
                // The probe validates the *saved* address and token, so require
                // both and block while an unsaved edit lingers (Save it first).
                disabled={
                  test.isPending ||
                  !query.data.bot_token_mask ||
                  !query.data.base_url ||
                  draft !== null
                }
                onClick={() => {
                  test.mutate();
                }}
              >
                {test.isPending && <LoaderCircleIcon className="animate-spin" />}
                {t("admin.platform.mattermost.test")}
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
