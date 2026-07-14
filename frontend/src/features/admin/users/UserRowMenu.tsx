import { useState } from "react";
import { useTranslation } from "react-i18next";

import { RowActionsMenu } from "@/components/list-controls/RowActions";
import {
  DropdownMenuItem,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
} from "@/components/ui/dropdown-menu";
import { isOwner as isOwnerRole, isMember } from "@/features/auth/roles";
import { useSession } from "@/features/auth/session-context";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ROLES, roleLabel } from "./format";
import { TempPasswordDialog } from "./TempPasswordDialog";
import type { AdminUser } from "./types";
import { useUserActions } from "./useUserActions";

type RowDialog = "deactivate" | "reset" | "delete" | null;

/** The ⋯ menu of a list row (users.html, legend 7): change role ·
 * deactivate/reactivate · reset password · delete. Rights mirror the card —
 * role change and delete are Owner-only, self-deactivation is hidden. */
export function UserRowMenu({ user }: { user: AdminUser }) {
  const { t } = useTranslation();
  const session = useSession();
  const isOwner = isOwnerRole(session.user?.role);
  const isSelf = session.user?.id === user.id;
  // Owner manages anyone; an admin only members (users_admin.manage_scope_or_403).
  const canManage = isOwner || isMember(user.role);
  const [dialog, setDialog] = useState<RowDialog>(null);
  const [tempPassword, setTempPassword] = useState<string | null>(null);
  const { patch, reset, remove } = useUserActions(user, { onTempPassword: setTempPassword });

  const close = () => {
    setDialog(null);
  };

  // Role change is owner-only; deactivate/reset need manage scope and never
  // target oneself; delete is owner-only and never oneself. With nothing left to
  // offer, drop the ⋯ trigger rather than open an empty menu.
  const canDeactivateOrReset = !isSelf && canManage;
  if (!isOwner && !canDeactivateOrReset) return null;

  return (
    <>
      <RowActionsMenu label={t("admin.users.rowMenu.label")}>
        {isOwner && (
          <DropdownMenuSub>
            <DropdownMenuSubTrigger>{t("admin.users.rowMenu.changeRole")}</DropdownMenuSubTrigger>
            <DropdownMenuSubContent>
              <DropdownMenuRadioGroup
                value={user.role}
                onValueChange={(value) => {
                  const role = ROLES.find((candidate) => candidate === value);
                  if (role && role !== user.role) patch.mutate({ role });
                }}
              >
                {ROLES.map((role) => (
                  <DropdownMenuRadioItem key={role} value={role}>
                    {roleLabel(role, t)}
                  </DropdownMenuRadioItem>
                ))}
              </DropdownMenuRadioGroup>
            </DropdownMenuSubContent>
          </DropdownMenuSub>
        )}
        {canDeactivateOrReset &&
          (user.status === "active" ? (
            <DropdownMenuItem
              onClick={() => {
                setDialog("deactivate");
              }}
            >
              {t("admin.users.card.deactivate")}
            </DropdownMenuItem>
          ) : (
            <DropdownMenuItem
              onClick={() => {
                patch.mutate({ status: "active" });
              }}
            >
              {t("admin.users.card.reactivate")}
            </DropdownMenuItem>
          ))}
        {canDeactivateOrReset && (
          <DropdownMenuItem
            onClick={() => {
              setDialog("reset");
            }}
          >
            {t("admin.users.card.resetPassword")}
          </DropdownMenuItem>
        )}
        {isOwner && !isSelf && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              variant="destructive"
              onClick={() => {
                setDialog("delete");
              }}
            >
              {t("admin.users.card.delete")}
            </DropdownMenuItem>
          </>
        )}
      </RowActionsMenu>

      <ConfirmDialog
        open={dialog === "deactivate"}
        onOpenChange={close}
        title={t("admin.users.card.deactivateConfirmTitle")}
        description={t("admin.users.card.deactivateConfirmBody", { email: user.email })}
        confirmLabel={t("admin.users.card.deactivate")}
        pending={patch.isPending}
        onConfirm={() => {
          patch.mutate({ status: "deactivated" }, { onSuccess: close });
        }}
      />
      <ConfirmDialog
        open={dialog === "reset"}
        onOpenChange={close}
        title={t("admin.users.card.resetPassword")}
        description={t("admin.users.card.resetConfirmBody", { email: user.email })}
        confirmLabel={t("admin.users.card.resetConfirmAction")}
        pending={reset.isPending}
        onConfirm={() => {
          reset.mutate(undefined, { onSuccess: close });
        }}
      />
      <ConfirmDialog
        open={dialog === "delete"}
        onOpenChange={close}
        title={t("admin.users.card.deleteConfirmTitle")}
        description={t("admin.users.card.deleteConfirmBody", { email: user.email })}
        confirmLabel={t("admin.users.card.delete")}
        destructive
        challenge={user.email}
        challengeLabel={t("admin.users.card.deleteTypeToConfirm")}
        pending={remove.isPending}
        onConfirm={() => {
          remove.mutate(undefined, { onSuccess: close });
        }}
      />
      <TempPasswordDialog
        password={tempPassword}
        onClose={() => {
          setTempPassword(null);
        }}
      />
    </>
  );
}
