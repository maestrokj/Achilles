import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactElement } from "react";
import { useTranslation } from "react-i18next";

import { toastApiError } from "@/api/errors";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

import { linkPrincipal, mappingCandidates, unlinkPrincipal, usersKeys } from "./api";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { MappingLink, MappingSource } from "./types";

/** One control for both states of a user × source cell (users.html#identity-mapping):
 * candidate search + link, and — when a link exists — "Unlink" inside the same
 * popover, so the admin overrides an auto-match in one step. */
export function IdentityLinkPopover({
  userId,
  source,
  current = null,
  trigger,
}: {
  userId: number;
  source: MappingSource;
  /** The existing link when the cell is matched; null for unmatched. */
  current?: MappingLink | null;
  trigger: ReactElement;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [confirmUnlink, setConfirmUnlink] = useState(false);
  const [q, setQ] = useState("");
  const candidates = useQuery({
    queryKey: usersKeys.candidates(source.id, q),
    queryFn: () => mappingCandidates(source.id, q),
    enabled: open,
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["admin", "identity-mapping"] });
  const link = useMutation({
    mutationFn: (principalId: number) =>
      linkPrincipal({ principal_id: principalId, user_id: userId }),
    onSuccess: () => {
      setOpen(false);
      void invalidate();
    },
    onError: (error) => void toastApiError(error, t("admin.users.card.actionFailed")),
  });
  const unlink = useMutation({
    mutationFn: () => unlinkPrincipal(current?.principal_id ?? 0),
    onSuccess: () => {
      setConfirmUnlink(false);
      setOpen(false);
      void invalidate();
    },
    onError: (error) => void toastApiError(error, t("admin.users.card.actionFailed")),
  });

  return (
    <>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger render={trigger} />
        <PopoverContent className="flex w-64 flex-col gap-2" align="start">
          <Input
            value={q}
            placeholder={t("admin.users.identity.candidatePlaceholder")}
            onChange={(event) => {
              setQ(event.target.value);
            }}
          />
          <div className="flex max-h-48 flex-col gap-1 overflow-y-auto">
            {(candidates.data?.items ?? []).map((candidate) => (
              <Button
                key={candidate.id}
                variant="ghost"
                size="sm"
                className="justify-start"
                disabled={link.isPending || candidate.id === current?.principal_id}
                onClick={() => {
                  link.mutate(candidate.id);
                }}
              >
                <span className="truncate">
                  {candidate.display_name ?? candidate.email ?? candidate.source_user_id}
                </span>
                {candidate.id === current?.principal_id ? (
                  <span className="text-muted-foreground ml-auto text-xs">
                    {t("admin.users.identity.current")}
                  </span>
                ) : (
                  candidate.linked_user_id !== null && (
                    <span className="text-muted-foreground ml-auto text-xs">
                      {t("admin.users.identity.taken")}
                    </span>
                  )
                )}
              </Button>
            ))}
            {candidates.data?.items.length === 0 && (
              <p className="text-muted-foreground p-2 text-xs">{t("common.list.empty")}</p>
            )}
          </div>
          {current && (
            <>
              <hr className="border-border -mx-2" />
              <Button
                variant="ghost"
                size="sm"
                className="text-destructive justify-start"
                onClick={() => {
                  setConfirmUnlink(true);
                }}
              >
                {t("admin.users.identity.unlink")}
              </Button>
            </>
          )}
        </PopoverContent>
      </Popover>
      <ConfirmDialog
        open={confirmUnlink}
        onOpenChange={setConfirmUnlink}
        title={t("admin.users.identity.unlinkConfirmTitle")}
        description={t("admin.users.identity.unlinkConfirmBody", {
          account: current?.display_name ?? current?.email ?? current?.source_user_id ?? "",
          source: source.name,
        })}
        confirmLabel={t("admin.users.identity.unlink")}
        destructive
        pending={unlink.isPending}
        onConfirm={() => {
          unlink.mutate();
        }}
      />
    </>
  );
}
