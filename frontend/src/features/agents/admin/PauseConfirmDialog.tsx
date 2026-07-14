import { useTranslation } from "react-i18next";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

/** One governance confirm for both admin screens; copy switches on the current state. */
export function PauseConfirmDialog({
  paused,
  open,
  onOpenChange,
  onConfirm,
}: {
  paused: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
}) {
  const { t } = useTranslation();
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            {paused
              ? t("admin.agents.actions.unpauseConfirmTitle")
              : t("admin.agents.actions.pauseConfirmTitle")}
          </AlertDialogTitle>
          <AlertDialogDescription>
            {paused
              ? t("admin.agents.actions.unpauseConfirmBody")
              : t("admin.agents.actions.pauseConfirmBody")}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>{t("admin.agents.actions.cancel")}</AlertDialogCancel>
          <AlertDialogAction onClick={onConfirm}>
            {t("admin.agents.actions.confirm")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
