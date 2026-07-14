import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toast } from "@/lib/toast";

import { toastApiError } from "@/api/errors";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/list-controls/EmptyState";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";

import { getPrefs, notificationKeys, putPrefs } from "./api";
import type { Pref } from "./types";

/** Personal narrowing: two independent switches (in-app · email) per category.
 * Members see the personal categories; admins see the whole matrix.
 * Wireframe: auth-security/profile-account.html#notifications. */
export function NotificationPrefs() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const prefs = useQuery({ queryKey: notificationKeys.prefs, queryFn: getPrefs });

  const save = useMutation({
    mutationFn: (item: Pref) => putPrefs([item]),
    onSuccess: (fresh) => {
      queryClient.setQueryData(notificationKeys.prefs, fresh);
      toast.success(t("notifications.prefs.saved"));
    },
    onError: (error) => void toastApiError(error, t("notifications.prefs.saveFailed")),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("notifications.prefs.title")}</CardTitle>
      </CardHeader>
      <CardContent>
        {prefs.isPending ? (
          <Skeleton className="h-24 w-full" />
        ) : prefs.isError ? (
          <EmptyState
            variant="error"
            description={t("common.list.errorTitle")}
            onRetry={() => {
              void prefs.refetch();
            }}
          />
        ) : (
          <div className="flex flex-col">
            <div className="text-muted-foreground grid grid-cols-[1fr_6rem_6rem] items-center gap-2 pb-2 text-xs font-medium">
              <span>{t("notifications.prefs.category")}</span>
              <span className="text-center">{t("notifications.prefs.inApp")}</span>
              <span className="text-center">{t("notifications.prefs.email")}</span>
            </div>
            <div className="divide-border border-border divide-y border-t">
              {prefs.data.items.map((item) => (
                <div
                  key={item.event_type}
                  className="grid h-11 grid-cols-[1fr_6rem_6rem] items-center gap-2"
                >
                  <span className="text-sm">{t(`notifications.types.${item.event_type}`)}</span>
                  <span className="flex justify-center">
                    <Switch
                      checked={item.in_app_enabled}
                      disabled={save.isPending}
                      onCheckedChange={(next) => {
                        save.mutate({ ...item, in_app_enabled: next });
                      }}
                    />
                  </span>
                  <span className="flex justify-center">
                    <Switch
                      checked={item.email_enabled}
                      disabled={save.isPending}
                      onCheckedChange={(next) => {
                        save.mutate({ ...item, email_enabled: next });
                      }}
                    />
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
