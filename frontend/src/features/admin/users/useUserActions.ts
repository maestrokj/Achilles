import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toast } from "@/lib/toast";

import { toastApiError } from "@/api/errors";

import { deleteUser, patchUser, resetPassword, usersKeys } from "./api";
import type { AdminUser } from "./types";

/** Admin actions over one user — shared by the card and the list row menu.
 * Backend enforces the manage scope; here we only wire mutations + refresh. */
export function useUserActions(
  user: Pick<AdminUser, "id">,
  { onDeleted, onTempPassword }: { onDeleted?: () => void; onTempPassword?: (raw: string) => void },
) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: usersKeys.detail(user.id) });
    void queryClient.invalidateQueries({ queryKey: ["admin", "users", "list"] });
    // Deactivation cascades on the backend — sessions end, personal agents pause
    // and API keys are revoked (users_admin.deactivate_cascade). The card's keys
    // section reads a query of its own, so refresh it too or it shows revoked
    // keys as still active until the next manual reload.
    void queryClient.invalidateQueries({ queryKey: usersKeys.keys(user.id) });
  };

  const patch = useMutation({
    mutationFn: (body: Parameters<typeof patchUser>[1]) => patchUser(user.id, body),
    onSuccess: invalidate,
    onError: (error) => void toastApiError(error, t("admin.users.card.actionFailed")),
  });

  const reset = useMutation({
    mutationFn: () => resetPassword(user.id),
    onSuccess: (data) => {
      // Primary path with SMTP: the letter is out — nothing to show once.
      if (data.mode === "link") toast.success(t("admin.users.card.resetLinkSent"));
      else if (data.temp_password) onTempPassword?.(data.temp_password);
    },
    onError: (error) => void toastApiError(error, t("admin.users.card.actionFailed")),
  });

  const remove = useMutation({
    mutationFn: () => deleteUser(user.id),
    onSuccess: () => {
      toast.success(t("admin.users.card.deleted"));
      invalidate();
      onDeleted?.();
    },
    onError: (error) => void toastApiError(error, t("admin.users.card.actionFailed")),
  });

  return { patch, reset, remove };
}
