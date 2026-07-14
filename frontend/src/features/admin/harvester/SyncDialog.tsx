import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toastApiError } from "@/api/errors";
import { toast } from "@/lib/toast";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";

import { harvesterKeys, startSync } from "./api";

/** Manual run modal: incremental vs full, with the duration warning on full.
 * Wireframe: admin-panel/_wireframes/data-sources.html#sync-wizard. */
export function SyncDialog({
  sourceId,
  open,
  onOpenChange,
}: {
  sourceId: number;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<"incremental" | "full">("incremental");

  const run = useMutation({
    mutationFn: () => startSync(sourceId, mode),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: harvesterKeys.sources });
      void queryClient.invalidateQueries({ queryKey: harvesterKeys.source(sourceId) });
      onOpenChange(false);
      toast.success(t("admin.harvester.syncStarted"));
    },
    onError: (error) => void toastApiError(error, t("admin.harvester.syncFailed")),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="p-6 sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("admin.harvester.syncDialog.title")}</DialogTitle>
        </DialogHeader>
        <RadioGroup
          className="gap-2"
          value={mode}
          onValueChange={(value: unknown) => {
            if (value === "incremental" || value === "full") setMode(value);
          }}
        >
          <div
            className={`hover:bg-muted/40 flex items-center gap-2.5 rounded-lg border px-3 py-2.5 transition-colors ${
              mode === "incremental" ? "border-primary/40 bg-muted/40" : ""
            }`}
          >
            <RadioGroupItem value="incremental" id="sync-incremental" />
            <Label className="flex-1 cursor-pointer" htmlFor="sync-incremental">
              {t("admin.harvester.syncDialog.incremental")}
            </Label>
          </div>
          <div
            className={`hover:bg-muted/40 flex items-center gap-2.5 rounded-lg border px-3 py-2.5 transition-colors ${
              mode === "full" ? "border-primary/40 bg-muted/40" : ""
            }`}
          >
            <RadioGroupItem value="full" id="sync-full" />
            <Label className="flex-1 cursor-pointer" htmlFor="sync-full">
              {t("admin.harvester.syncDialog.full")}
            </Label>
          </div>
        </RadioGroup>
        {mode === "full" && (
          <p className="bg-warning/10 text-warning rounded-lg px-3 py-2 text-xs">
            {t("admin.harvester.syncDialog.fullWarning")}
          </p>
        )}
        <DialogFooter className="pt-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              onOpenChange(false);
            }}
          >
            {t("admin.platform.cancel")}
          </Button>
          <Button
            size="sm"
            disabled={run.isPending}
            onClick={() => {
              run.mutate();
            }}
          >
            {t("admin.harvester.syncDialog.start")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
