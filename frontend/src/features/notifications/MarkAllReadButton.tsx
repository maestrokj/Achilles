import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";

import { markAllRead } from "./api";

/** The accented bulk action, lifted out of the feed card into the page header
 * so both the personal and admin inbox share one home for it. Invalidating the
 * "notifications" root refreshes the feed and the bell badge alike. */
export function MarkAllReadButton() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const readAll = useMutation({
    mutationFn: markAllRead,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["notifications"] }),
  });
  return (
    <Button
      size="sm"
      className="shrink-0"
      disabled={readAll.isPending}
      onClick={() => {
        readAll.mutate();
      }}
    >
      {t("notifications.readAll")}
    </Button>
  );
}
