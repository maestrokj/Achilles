import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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
  createKey,
  listUsers,
  USER_SUGGEST_LIMIT,
  USER_SUGGEST_PAGE_SIZE,
  usersKeys,
} from "@/features/admin/users/api";
import type { AdminUser } from "@/features/admin/users/types";
import {
  API_KEY_EXPIRY_CHOICES,
  API_KEY_NAME_MAX_LEN,
  expiryPayload,
  type ApiKeyExpiry,
} from "@/features/auth/api-keys";

/** The shared key-issuance modal (api-key-create-modal.html): API Keys screen
 * picks an employee by email; the user card passes `fixedUser` and skips the
 * lookup. The key inherits the owner's rights; the secret is shown once. */
export function CreateKeyDialog({
  open,
  onOpenChange,
  fixedUser,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Pre-selected owner — the dialog was opened from that user's card. */
  fixedUser?: Pick<AdminUser, "id" | "full_name" | "email">;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [emailInput, setEmailInput] = useState("");
  const [picked, setPicked] = useState<AdminUser | null>(null);
  const [name, setName] = useState("");
  const [expiry, setExpiry] = useState<ApiKeyExpiry>("none");
  const [createdKey, setCreatedKey] = useState<string | null>(null);

  // Suggestions ride the backend search — a thousand-user company never loads
  // the whole directory into a dropdown.
  const search = emailInput.trim();
  const usersQuery = { page: 1, per_page: USER_SUGGEST_PAGE_SIZE, q: search };
  const users = useQuery({
    queryKey: usersKeys.list(usersQuery),
    queryFn: () => listUsers(usersQuery),
    enabled: open && !fixedUser && picked === null && search.length >= 2,
    placeholderData: keepPreviousData,
  });
  const suggestions = (users.data?.items ?? []).filter((user) => user.status === "active");
  const exact = suggestions.find((user) => user.email.toLowerCase() === search.toLowerCase());
  const target = fixedUser ?? picked ?? exact ?? null;

  const issue = useMutation({
    mutationFn: () =>
      createKey({ user_id: target?.id, name: name.trim() || undefined, ...expiryPayload(expiry) }),
    onSuccess: (data) => {
      setCreatedKey(data.key);
      void queryClient.invalidateQueries({ queryKey: ["admin", "api-keys"] });
      if (target) void queryClient.invalidateQueries({ queryKey: usersKeys.keys(target.id) });
    },
  });

  const close = (next: boolean) => {
    if (!next) {
      setEmailInput("");
      setPicked(null);
      setName("");
      setExpiry("none");
      setCreatedKey(null);
    }
    onOpenChange(next);
  };

  const expiryLabel = (choice: ApiKeyExpiry) =>
    choice === "none"
      ? t("admin.apiKeys.noExpiry")
      : t("admin.apiKeys.expiryDays", { count: Number(choice) });

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>{t("admin.apiKeys.create")}</DialogTitle>
          <DialogDescription>{t("admin.apiKeys.createHint")}</DialogDescription>
        </DialogHeader>
        {createdKey ? (
          <div className="flex flex-col gap-2">
            <code className="bg-muted rounded-md p-3 text-center text-sm break-all">
              {createdKey}
            </code>
            <p className="text-muted-foreground text-xs">{t("admin.users.card.keyShownOnce")}</p>
          </div>
        ) : (
          <div className="flex flex-col gap-4">
            {fixedUser ? (
              <div className="flex flex-col gap-0.5">
                <span className="text-sm font-medium">{fixedUser.full_name}</span>
                <span className="text-muted-foreground text-xs">{fixedUser.email}</span>
              </div>
            ) : (
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="create-key-email">{t("admin.apiKeys.userLabel")}</Label>
                <Input
                  id="create-key-email"
                  type="email"
                  autoComplete="off"
                  placeholder={t("admin.apiKeys.userPlaceholder")}
                  value={emailInput}
                  onChange={(event) => {
                    setEmailInput(event.target.value);
                    setPicked(null);
                  }}
                />
                {picked === null && search.length >= 2 && suggestions.length > 0 && !exact && (
                  <div className="divide-border divide-y overflow-hidden rounded-lg border">
                    {suggestions.slice(0, USER_SUGGEST_LIMIT).map((user) => (
                      <button
                        key={user.id}
                        type="button"
                        className="hover:bg-muted/60 flex w-full flex-col items-start px-3 py-1.5 text-left transition-colors"
                        onClick={() => {
                          setPicked(user);
                          setEmailInput(user.email);
                        }}
                      >
                        <span className="text-sm font-medium">{user.full_name}</span>
                        <span className="text-muted-foreground text-xs">{user.email}</span>
                      </button>
                    ))}
                  </div>
                )}
                {target && <p className="text-muted-foreground text-xs">{target.full_name}</p>}
              </div>
            )}
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="create-key-name">{t("admin.apiKeys.nameLabel")}</Label>
              <Input
                id="create-key-name"
                autoComplete="off"
                maxLength={API_KEY_NAME_MAX_LEN}
                placeholder={t("admin.apiKeys.namePlaceholder")}
                value={name}
                onChange={(event) => {
                  setName(event.target.value);
                }}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>{t("admin.apiKeys.expiryLabel")}</Label>
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
              disabled={target === null || issue.isPending}
              onClick={() => {
                issue.mutate();
              }}
            >
              {t("admin.apiKeys.createAction")}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
