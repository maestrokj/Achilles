import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toast } from "@/lib/toast";

import { LoaderCircleIcon, MailIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { EmptyState } from "@/components/list-controls/EmptyState";
import { InfoHint } from "@/components/InfoHint";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";

import { getSmtpSettings, patchSmtpSettings, platformKeys, testSmtpConnection } from "./api";
import { SectionCard } from "./SectionCard";
import { StatusBadge } from "./StatusBadge";
import type { SmtpSecurity } from "./types";
import { useIntegrationCard } from "./useIntegrationCard";

const SECURITY_MODES: SmtpSecurity[] = ["none", "starttls", "ssl_tls"];

/** The #smtp section of the Platform screen (platform-settings.html#smtp):
 * write-only password shown as a mask, an inline "test connection" that sends
 * a real letter to the acting admin, and the master switch. Owner edits. */
export function SmtpCard({ readOnly }: { readOnly: boolean }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { query, save, test, draft, setDraft, saveDraft } = useIntegrationCard({
    queryKey: platformKeys.smtp,
    get: getSmtpSettings,
    patch: patchSmtpSettings,
    test: testSmtpConnection,
    // smtp_configured on /admin/settings gates the invites tab — keep it honest.
    onSaved: () => void queryClient.invalidateQueries({ queryKey: platformKeys.settings }),
    onTestResult: (result) => {
      if (result.ok) toast.success(t("admin.platform.smtp.testOk"));
      else
        toast.error(
          t("admin.platform.smtp.testFailed", {
            error: result.error ?? t("admin.platform.smtp.testErrorUnknown"),
          }),
        );
    },
  });

  return (
    <SectionCard
      id="smtp"
      icon={MailIcon}
      title={t("admin.platform.smtp.title")}
      subtitle={t("admin.platform.smtp.subtitle")}
      aside={
        query.data && (
          <div className="flex items-center gap-3">
            <StatusBadge
              ok={query.data.last_test_ok}
              pending={test.isPending}
              pendingLabel={t("admin.platform.testing")}
              labels={{
                ok: t("admin.platform.smtp.statusOk"),
                failed: t("admin.platform.smtp.statusFailed"),
                untested: t("admin.platform.smtp.neverTested"),
              }}
            />
            <Switch
              checked={query.data.is_enabled}
              disabled={readOnly || save.isPending}
              onCheckedChange={(next) => {
                save.mutate({ is_enabled: next });
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
          <div className="flex flex-wrap items-end gap-4">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="smtp-host">{t("admin.platform.smtp.host")}</Label>
              <Input
                id="smtp-host"
                className="w-56"
                placeholder="smtp.company.com"
                value={draft?.host ?? query.data.host ?? ""}
                disabled={readOnly}
                onChange={(e) => {
                  setDraft({ ...draft, host: e.target.value });
                }}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="smtp-port">{t("admin.platform.smtp.port")}</Label>
              <Input
                id="smtp-port"
                className="w-24"
                type="number"
                placeholder="587"
                value={draft?.port !== undefined ? (draft.port ?? "") : (query.data.port ?? "")}
                disabled={readOnly}
                onChange={(e) => {
                  setDraft({
                    ...draft,
                    port: e.target.value === "" ? null : Number(e.target.value),
                  });
                }}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>{t("admin.platform.smtp.security")}</Label>
              <Select
                items={SECURITY_MODES.map((mode) => ({
                  value: mode,
                  label: t(`admin.platform.smtp.securityMode.${mode}`),
                }))}
                value={draft?.security ?? query.data.security}
                onValueChange={(value) => {
                  setDraft({ ...draft, security: value as SmtpSecurity });
                }}
              >
                <SelectTrigger className="w-32" disabled={readOnly}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {SECURITY_MODES.map((mode) => (
                    <SelectItem key={mode} value={mode}>
                      {t(`admin.platform.smtp.securityMode.${mode}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="flex flex-wrap items-end gap-4">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="smtp-username">{t("admin.platform.smtp.username")}</Label>
              <Input
                id="smtp-username"
                className="w-56"
                autoComplete="off"
                value={draft?.username ?? query.data.username ?? ""}
                disabled={readOnly}
                onChange={(e) => {
                  setDraft({ ...draft, username: e.target.value });
                }}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="smtp-password" className="flex items-center gap-1.5">
                {t("admin.platform.smtp.password")}
                <InfoHint text={t("admin.platform.smtp.passwordHint")} />
              </Label>
              <Input
                id="smtp-password"
                className="w-56"
                type="password"
                autoComplete="new-password"
                value={draft?.password ?? ""}
                placeholder={query.data.password_mask ?? t("admin.platform.smtp.passwordNotSet")}
                disabled={readOnly}
                onChange={(e) => {
                  setDraft({ ...draft, password: e.target.value });
                }}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="smtp-from">{t("admin.platform.smtp.fromAddress")}</Label>
              <Input
                id="smtp-from"
                className="w-72"
                placeholder="Achilles <no-reply@company.com>"
                value={draft?.from_address ?? query.data.from_address ?? ""}
                disabled={readOnly}
                onChange={(e) => {
                  setDraft({ ...draft, from_address: e.target.value });
                }}
              />
            </div>
          </div>

          {!readOnly && (
            <div className="flex justify-end gap-2">
              <Button
                size="sm"
                variant="outline"
                // The test sends via the *saved* settings — block while a draft lingers.
                disabled={test.isPending || !query.data.is_available || draft !== null}
                onClick={() => {
                  test.mutate();
                }}
              >
                {test.isPending && <LoaderCircleIcon className="animate-spin" />}
                {t("admin.platform.smtp.test")}
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
