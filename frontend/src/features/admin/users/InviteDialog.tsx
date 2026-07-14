import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "@/lib/toast";

import { apiErrorReason } from "@/api/errors";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { createInvite } from "./api";
import { roleLabel } from "./format";

export function InviteDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");
  const [error, setError] = useState<string | null>(null);

  // The dialog stays mounted, so clear a stale error/draft on every open→ transition
  // (the render-phase reset React recommends over an effect).
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) {
      setEmail("");
      setError(null);
    }
  }

  const send = useMutation({
    mutationFn: () => createInvite({ email: email.trim(), role }),
    onSuccess: () => {
      toast.success(t("admin.users.invites.sent"));
      void queryClient.invalidateQueries({ queryKey: ["admin", "invites"] });
      setEmail("");
      setError(null);
      onOpenChange(false);
    },
    onError: async (err) => {
      setError(await apiErrorReason(err));
    },
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-sm p-6">
        <DialogHeader>
          <DialogTitle>{t("admin.users.invites.dialogTitle")}</DialogTitle>
          <DialogDescription>{t("admin.users.invites.dialogHint")}</DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="invite-email">{t("admin.users.columns.email")}</Label>
            <Input
              id="invite-email"
              type="email"
              value={email}
              placeholder="colleague@company.com"
              onChange={(event) => {
                setEmail(event.target.value);
              }}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>{t("admin.users.columns.role")}</Label>
            <Select
              items={["member", "admin", "owner"].map((value) => ({
                value,
                label: roleLabel(value, t),
              }))}
              value={role}
              onValueChange={(value) => {
                if (value) setRole(value);
              }}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {["member", "admin", "owner"].map((value) => (
                  <SelectItem key={value} value={value}>
                    {roleLabel(value, t)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {error && <p className="text-destructive text-sm">{error}</p>}
        </div>
        <DialogFooter className="-mx-6 -mb-6 px-6 py-4">
          <Button
            variant="ghost"
            onClick={() => {
              onOpenChange(false);
            }}
          >
            {t("common.cancel")}
          </Button>
          <Button
            disabled={!email.includes("@") || send.isPending}
            onClick={() => {
              send.mutate();
            }}
          >
            {t("admin.users.invites.send")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
