import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "@/lib/toast";

import { toastApiError } from "@/api/errors";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
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
import { createWebhook, patchChannel } from "@/features/notifications/api";
import type { Channel, WebhookPreset } from "@/features/notifications/types";

/** The webhook modal: preset (Slack / Generic) + endpoint + optional secret.
 * With `channel` it edits in place: secrets are write-only, so URL and secret
 * come back empty — empty means "leave as is", input replaces (wireframe legend 9).
 * Wireframe: admin-panel/_wireframes/notifications.html#add-webhook. */
export function AddWebhookDialog({
  open,
  onOpenChange,
  onCreated,
  channel,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: () => void;
  /** Channel being edited; omitted for creation. */
  channel?: Channel | null;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="p-6 sm:max-w-md">
        {/* Remount per target (or the blank create form) so each opening starts
            from the target's current state — no setState-in-effect sync. */}
        {open && (
          <WebhookForm
            key={channel?.id ?? "new"}
            channel={channel ?? null}
            onOpenChange={onOpenChange}
            onCreated={onCreated}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

function WebhookForm({
  channel,
  onOpenChange,
  onCreated,
}: {
  channel: Channel | null;
  onOpenChange: (open: boolean) => void;
  onCreated: () => void;
}) {
  const { t } = useTranslation();
  const editing = channel != null;
  const [name, setName] = useState(channel?.name ?? "");
  const [preset, setPreset] = useState<WebhookPreset>(channel?.preset ?? "slack");
  const [url, setUrl] = useState("");
  const [secret, setSecret] = useState("");

  const save = useMutation({
    mutationFn: () =>
      channel
        ? patchChannel(channel.id, {
            name,
            ...(url ? { url } : {}),
            ...(channel.preset === "generic" && secret ? { secret } : {}),
          })
        : createWebhook({
            name,
            preset,
            url,
            ...(preset === "generic" && secret ? { secret } : {}),
          }),
    onSuccess: () => {
      toast.success(
        editing ? t("admin.notifications.webhookUpdated") : t("admin.notifications.webhookCreated"),
      );
      onCreated();
      onOpenChange(false);
    },
    onError: (error) => void toastApiError(error, t("admin.platform.saveFailed")),
  });

  const secretVisible = editing ? channel.preset === "generic" : preset === "generic";

  return (
    <>
      <DialogHeader>
        <DialogTitle>
          {editing ? t("admin.notifications.editWebhook") : t("admin.notifications.addWebhook")}
        </DialogTitle>
      </DialogHeader>
      <div className="flex flex-col gap-4">
        <div className="flex flex-col gap-1.5">
          <Label>{t("admin.notifications.webhookPreset")}</Label>
          {editing ? (
            // The preset fixes the payload format at creation — it is not editable.
            <p className="text-muted-foreground text-sm">
              {channel.preset === "generic" ? t("admin.notifications.presetGeneric") : "Slack"}
            </p>
          ) : (
            <Select
              items={[
                { value: "slack", label: "Slack" },
                { value: "generic", label: t("admin.notifications.presetGeneric") },
              ]}
              value={preset}
              onValueChange={(value) => {
                setPreset(value as WebhookPreset);
              }}
            >
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="slack">Slack</SelectItem>
                <SelectItem value="generic">{t("admin.notifications.presetGeneric")}</SelectItem>
              </SelectContent>
            </Select>
          )}
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="webhook-name">{t("admin.notifications.webhookName")}</Label>
          <Input
            id="webhook-name"
            value={name}
            onChange={(e) => {
              setName(e.target.value);
            }}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="webhook-url">{t("admin.notifications.webhookUrl")}</Label>
          <Input
            id="webhook-url"
            placeholder={editing ? (channel.url_mask ?? "") : "https://hooks.slack.com/services/…"}
            value={url}
            onChange={(e) => {
              setUrl(e.target.value);
            }}
          />
          {editing && (
            <p className="text-muted-foreground text-xs">
              {t("admin.notifications.webhookUrlKeepHint")}
            </p>
          )}
        </div>
        {secretVisible && (
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="webhook-secret">{t("admin.notifications.webhookSecret")}</Label>
            <Input
              id="webhook-secret"
              type="password"
              autoComplete="off"
              value={secret}
              onChange={(e) => {
                setSecret(e.target.value);
              }}
            />
            <p className="text-muted-foreground text-xs">
              {editing && channel.secret_set
                ? t("admin.notifications.webhookSecretKeepHint")
                : t("admin.notifications.webhookSecretHint")}
            </p>
          </div>
        )}
      </div>
      <DialogFooter className="-mx-6 -mb-6 px-6 py-4">
        <Button
          variant="outline"
          onClick={() => {
            onOpenChange(false);
          }}
        >
          {t("common.cancel")}
        </Button>
        <Button
          disabled={!name || (!editing && !url) || save.isPending}
          onClick={() => {
            save.mutate();
          }}
        >
          {editing ? t("admin.notifications.save") : t("admin.notifications.create")}
        </Button>
      </DialogFooter>
    </>
  );
}
