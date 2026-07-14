import { useTranslation } from "react-i18next";

import { CopyButton } from "@/components/ui/copy-button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

/** Temp-password fallback of the admin reset (no SMTP): shown exactly once —
 * user-card.html#temp-password. */
export function TempPasswordDialog({
  password,
  onClose,
}: {
  password: string | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Dialog
      open={password !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>{t("admin.users.card.tempPasswordTitle")}</DialogTitle>
          <DialogDescription>{t("admin.users.card.tempPasswordHint")}</DialogDescription>
        </DialogHeader>
        <code className="bg-muted rounded-md p-3 text-center text-lg">{password}</code>
        <DialogFooter>{password && <CopyButton text={password} withLabel />}</DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
