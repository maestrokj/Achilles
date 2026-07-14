import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MonitorIcon, SmartphoneIcon } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "@/lib/toast";

import { toastApiError } from "@/api/errors";
import { BackLink } from "@/components/BackLink";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { formatWhen } from "@/lib/format";
import { parseUserAgent } from "@/lib/user-agent";

import {
  accountKeys,
  listSessions,
  revokeOtherSessions,
  revokeSession,
  type SessionInfo,
} from "./api";

/** /account/sessions — active devices with per-session revoke and "end all others".
 * Wireframe: auth-security/_wireframes/session-management.html. */
export function SessionsPage() {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const [revoking, setRevoking] = useState<SessionInfo | null>(null);
  const [endOthers, setEndOthers] = useState(false);

  const sessions = useQuery({ queryKey: accountKeys.sessions, queryFn: listSessions });
  const invalidate = () => queryClient.invalidateQueries({ queryKey: accountKeys.sessions });

  const revoke = useMutation({
    mutationFn: (id: string) => revokeSession(id),
    onSuccess: () => {
      setRevoking(null);
      void invalidate();
    },
    onError: (error) => void toastApiError(error, t("account.sessions.revokeFailed")),
  });
  const revokeOthers = useMutation({
    mutationFn: revokeOtherSessions,
    onSuccess: () => {
      setEndOthers(false);
      toast.success(t("account.sessions.othersEnded"));
      void invalidate();
    },
    onError: (error) => void toastApiError(error, t("account.sessions.revokeFailed")),
  });

  const items = sessions.data?.items ?? [];
  const hasOthers = items.some((s) => !s.is_current);

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex w-full max-w-2xl flex-col gap-4 px-6 py-8">
        <div className="flex flex-col gap-1">
          <BackLink to="/account" label={t("account.title")} />
          <div className="flex items-center justify-between gap-3">
            <h1 className="text-2xl font-semibold tracking-tight">{t("account.sessions.title")}</h1>
            {hasOthers && (
              <Button
                variant="outline"
                size="sm"
                className="shrink-0"
                onClick={() => {
                  setEndOthers(true);
                }}
              >
                {t("account.sessions.endOthers")}
              </Button>
            )}
          </div>
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-semibold">
              {t("account.sessions.activeTitle")}
            </CardTitle>
          </CardHeader>
          <CardContent>
            {sessions.isLoading ? (
              <div className="divide-border divide-y overflow-hidden rounded-lg border">
                {[0, 1].map((i) => (
                  <div key={i} className="flex items-center gap-3 px-3 py-2.5">
                    <Skeleton className="size-4 shrink-0 rounded-full" />
                    <div className="flex flex-col gap-1.5">
                      <Skeleton className="h-3.5 w-40" />
                      <Skeleton className="h-3 w-28" />
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="divide-border divide-y overflow-hidden rounded-lg border">
                {items.map((s) => {
                  const device = parseUserAgent(s.user_agent);
                  const label =
                    device.browser && device.os
                      ? t("account.sessions.deviceOn", { browser: device.browser, os: device.os })
                      : (device.browser ?? device.os ?? t("account.sessions.unknownDevice"));
                  const DeviceIcon =
                    device.os === "Android" || device.os === "iOS" ? SmartphoneIcon : MonitorIcon;
                  return (
                    <div key={s.id} className="flex items-center gap-3 px-3 py-2.5">
                      <DeviceIcon className="text-muted-foreground size-4 shrink-0" />
                      <div className="flex min-w-0 flex-col gap-0.5">
                        <span className="truncate text-sm font-medium">{label}</span>
                        <span className="text-muted-foreground truncate text-xs">
                          {[s.ip, formatWhen(s.created_at, i18n.language)]
                            .filter(Boolean)
                            .join(" · ")}
                        </span>
                      </div>
                      {s.is_current ? (
                        <Badge variant="secondary" className="ml-auto shrink-0">
                          {t("account.sessions.current")}
                        </Badge>
                      ) : (
                        <Button
                          variant="outline"
                          size="xs"
                          className="ml-auto shrink-0"
                          onClick={() => {
                            setRevoking(s);
                          }}
                        >
                          {t("account.sessions.revoke")}
                        </Button>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <ConfirmDialog
        open={revoking !== null}
        onOpenChange={(open) => {
          if (!open) setRevoking(null);
        }}
        title={t("account.sessions.revokeTitle")}
        description={t("account.sessions.revokeBody")}
        confirmLabel={t("account.sessions.revoke")}
        destructive
        pending={revoke.isPending}
        onConfirm={() => {
          if (revoking) revoke.mutate(revoking.id);
        }}
      />
      <ConfirmDialog
        open={endOthers}
        onOpenChange={setEndOthers}
        title={t("account.sessions.endOthersTitle")}
        description={t("account.sessions.endOthersBody")}
        confirmLabel={t("account.sessions.endOthers")}
        destructive
        pending={revokeOthers.isPending}
        onConfirm={() => {
          revokeOthers.mutate();
        }}
      />
    </div>
  );
}
