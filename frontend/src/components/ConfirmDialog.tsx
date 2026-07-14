import { useId, useState } from "react";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/** The guard modal of the wireframes (confirm-dialog.html): a plain confirm
 * for reversible actions, or type-to-confirm when `challenge` is given —
 * the admin retypes it (email/name) before the destructive action unlocks. */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  onConfirm,
  destructive = false,
  pending = false,
  challenge,
  challengeLabel,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  confirmLabel: string;
  onConfirm: () => void;
  destructive?: boolean;
  pending?: boolean;
  /** Exact text the admin must retype to unlock the confirm button. */
  challenge?: string;
  challengeLabel?: string;
}) {
  const { t } = useTranslation();
  const inputId = useId();
  const [typed, setTyped] = useState("");
  // Reset the challenge on every open. onOpenChange does not fire for the
  // controlled (programmatic) open, so we clear on the open→ transition here
  // (the render-phase reset React recommends over an effect).
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) setTyped("");
  }
  const locked = challenge !== undefined && typed.trim() !== challenge;

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{description}</AlertDialogDescription>
        </AlertDialogHeader>
        {challenge !== undefined && (
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={inputId}>{challengeLabel}</Label>
            <Input
              id={inputId}
              value={typed}
              autoComplete="off"
              placeholder={challenge}
              onChange={(event) => {
                setTyped(event.target.value);
              }}
            />
          </div>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel>{t("common.cancel")}</AlertDialogCancel>
          <AlertDialogAction
            variant={destructive ? "destructive" : "default"}
            disabled={locked || pending}
            onClick={onConfirm}
          >
            {confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
