import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { CopyButton } from "@/components/ui/copy-button";
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
import {
  API_KEY_EXPIRY_CHOICES,
  API_KEY_NAME_MAX_LEN,
  type ApiKeyExpiry,
} from "@/features/auth/api-keys";

import { accountKeys, createMyKey } from "./api";

/** Self-service twin of the admin's CreateKeyDialog: no owner picker — the key
 * belongs to the caller. Nothing is issued until "Create"; afterwards the secret
 * is shown once, inside the dialog. */
export function CreateMyKeyDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [expiry, setExpiry] = useState<ApiKeyExpiry>("none");
  const [createdKey, setCreatedKey] = useState<string | null>(null);

  const issue = useMutation({
    mutationFn: () => createMyKey(expiry, name.trim() || undefined),
    onSuccess: (data) => {
      setCreatedKey(data.key);
      void queryClient.invalidateQueries({ queryKey: accountKeys.apiKeys });
    },
  });

  const close = (next: boolean) => {
    if (!next) {
      setName("");
      setExpiry("none");
      setCreatedKey(null);
    }
    onOpenChange(next);
  };

  const expiryLabel = (choice: ApiKeyExpiry) =>
    choice === "none"
      ? t("account.keys.noExpiry")
      : t("account.keys.expiryDays", { count: Number(choice) });

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>
            {createdKey ? t("account.keys.createdTitle") : t("account.keys.create")}
          </DialogTitle>
          <DialogDescription>
            {createdKey ? t("account.keys.createdHint") : t("account.keys.createHint")}
          </DialogDescription>
        </DialogHeader>
        {createdKey ? (
          <code className="bg-muted rounded-md p-3 text-center font-mono text-sm break-all">
            {createdKey}
          </code>
        ) : (
          <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="create-my-key-name">{t("account.keys.nameLabel")}</Label>
              <Input
                id="create-my-key-name"
                autoComplete="off"
                maxLength={API_KEY_NAME_MAX_LEN}
                placeholder={t("account.keys.namePlaceholder")}
                value={name}
                onChange={(event) => {
                  setName(event.target.value);
                }}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>{t("account.keys.expiryLabel")}</Label>
              <Select
                items={API_KEY_EXPIRY_CHOICES.map((choice) => ({
                  value: choice,
                  label: expiryLabel(choice),
                }))}
                value={expiry}
                onValueChange={(value) => {
                  if (value) setExpiry(value);
                }}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {API_KEY_EXPIRY_CHOICES.map((choice) => (
                    <SelectItem key={choice} value={choice}>
                      {expiryLabel(choice)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
        )}
        <DialogFooter>
          {createdKey ? (
            <CopyButton text={createdKey} withLabel />
          ) : (
            <Button
              disabled={issue.isPending}
              onClick={() => {
                issue.mutate();
              }}
            >
              {t("account.keys.createAction")}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
