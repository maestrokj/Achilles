import { useTranslation } from "react-i18next";
import { toast } from "@/lib/toast";

import { HashIcon, LoaderCircleIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { EmptyState } from "@/components/list-controls/EmptyState";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";

import { getSlackSettings, patchSlackSettings, platformKeys, testSlackConnection } from "./api";
import { InfoHint } from "@/components/InfoHint";
import { SectionCard } from "./SectionCard";
import { StatusBadge } from "./StatusBadge";
import { useIntegrationCard } from "./useIntegrationCard";

const SLACK_TEST_ERROR_KEYS = ["no_token", "network_error", "invalid_auth"] as const;

/** The #slack section of the Platform screen (platform-settings.html#slack):
 * write-only secrets shown as masks, a live "test connection" probe that
 * stamps the workspace facts, and the master switch. Owner edits. */
export function SlackCard({ readOnly }: { readOnly: boolean }) {
  const { t } = useTranslation();
  const { query, save, test, draft, setDraft, saveDraft } = useIntegrationCard({
    queryKey: platformKeys.slack,
    get: getSlackSettings,
    patch: patchSlackSettings,
    test: testSlackConnection,
    onTestResult: (result) => {
      if (result.ok) toast.success(t("admin.platform.slack.testOk"));
      else {
        const raw = result.error;
        const error =
          raw === null
            ? t("admin.platform.slack.testErrorUnknown")
            : (SLACK_TEST_ERROR_KEYS as readonly string[]).includes(raw)
              ? t(
                  `admin.platform.slack.testErrors.${raw as (typeof SLACK_TEST_ERROR_KEYS)[number]}`,
                )
              : raw;
        toast.error(t("admin.platform.slack.testFailed", { error }));
      }
    },
  });

  return (
    <SectionCard
      id="surfaces"
      icon={HashIcon}
      title={t("admin.platform.slack.title")}
      subtitle={t("admin.platform.slack.subtitle")}
      aside={
        query.data && (
          <div className="flex items-center gap-3">
            <StatusBadge
              ok={query.data.last_test_ok}
              pending={test.isPending}
              pendingLabel={t("admin.platform.testing")}
              labels={{
                ok: t("admin.platform.slack.statusOk"),
                failed: t("admin.platform.slack.statusFailed"),
                untested: t("admin.platform.slack.neverTested"),
              }}
            />
            <Switch
              checked={query.data.enabled}
              disabled={readOnly || save.isPending}
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
              <Label htmlFor="slack-signing">{t("admin.platform.slack.signingSecret")}</Label>
              <Input
                id="slack-signing"
                className="w-64"
                type="password"
                autoComplete="off"
                value={draft?.signing_secret ?? ""}
                placeholder={
                  query.data.signing_secret_set
                    ? t("admin.platform.slack.secretSet")
                    : t("admin.platform.slack.secretNotSet")
                }
                disabled={readOnly}
                onChange={(e) => {
                  setDraft({ ...draft, signing_secret: e.target.value });
                }}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="slack-token">{t("admin.platform.slack.botToken")}</Label>
              <Input
                id="slack-token"
                className="w-64"
                type="password"
                autoComplete="off"
                value={draft?.bot_token ?? ""}
                placeholder={query.data.bot_token_mask ?? t("admin.platform.slack.secretNotSet")}
                disabled={readOnly}
                onChange={(e) => {
                  setDraft({ ...draft, bot_token: e.target.value });
                }}
              />
            </div>
          </div>

          {!readOnly && (
            <div className="flex justify-end gap-2">
              <Button
                size="sm"
                variant="outline"
                // The probe validates the *saved* token, so require one and
                // block while unsaved secret edits linger (Save them first),
                // else a valid typed-but-unsaved token reads as a failure.
                disabled={
                  test.isPending ||
                  !query.data.bot_token_mask ||
                  Boolean(draft?.bot_token || draft?.signing_secret)
                }
                onClick={() => {
                  test.mutate();
                }}
              >
                {test.isPending && <LoaderCircleIcon className="animate-spin" />}
                {t("admin.platform.slack.test")}
              </Button>
              <Button size="sm" disabled={draft === null || save.isPending} onClick={saveDraft}>
                {t("admin.platform.save")}
              </Button>
            </div>
          )}

          <div className="border-border/60 -mt-1 flex items-center justify-between gap-6 border-t pt-4">
            <p className="flex items-center gap-1.5 text-sm font-medium">
              {t("admin.platform.slack.autoLink")}
              <InfoHint
                text={t("admin.platform.slack.autoLinkHint")}
                label={t("admin.platform.slack.autoLink")}
              />
            </p>
            <Switch
              checked={query.data.auto_link_by_email}
              disabled={readOnly || save.isPending}
              onCheckedChange={(next) => {
                save.mutate({ auto_link_by_email: next });
              }}
            />
          </div>
        </>
      )}
    </SectionCard>
  );
}
