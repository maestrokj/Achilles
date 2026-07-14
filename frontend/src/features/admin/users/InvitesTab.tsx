import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MailIcon } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "@/lib/toast";

import { DataTable, TableFrame, TruncateCell } from "@/components/list-controls/DataTable";
import { EmptyState } from "@/components/list-controls/EmptyState";
import { FacetSelect } from "@/components/list-controls/FacetSelect";
import { Pagination } from "@/components/list-controls/Pagination";
import { RowActions } from "@/components/list-controls/RowActions";
import { SearchInput } from "@/components/list-controls/SearchInput";
import { TableSkeleton } from "@/components/list-controls/TableSkeleton";
import { buildListQuery, useListState } from "@/components/list-controls/useListState";
import { Badge } from "@/components/ui/badge";
import { TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

import { listInvites, resendInvite, revokeInvite, usersKeys } from "./api";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ROLES, roleLabel } from "./format";
import type { Invite, InviteStatus } from "./types";

const FACETS = ["status", "role"] as const;

const STATUS_TONE: Record<InviteStatus, "warning" | "success" | "destructive"> = {
  pending: "warning",
  accepted: "success",
  expired: "destructive",
};

export function InvitesTab({ smtpConfigured }: { smtpConfigured: boolean }) {
  const { t } = useTranslation();
  const list = useListState(FACETS);
  const queryClient = useQueryClient();
  const [revokeTarget, setRevokeTarget] = useState<Invite | null>(null);
  const query = buildListQuery(list);
  const invites = useQuery({
    queryKey: usersKeys.invites(query),
    queryFn: () => listInvites(query),
    placeholderData: keepPreviousData,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["admin", "invites"] });
  const resend = useMutation({
    mutationFn: resendInvite,
    onSuccess: () => {
      toast.success(t("admin.users.invites.resent"));
      void invalidate();
    },
  });
  const revoke = useMutation({
    mutationFn: revokeInvite,
    onSuccess: () => {
      toast.success(t("admin.users.invites.revoked"));
      setRevokeTarget(null);
      void invalidate();
    },
  });

  const rowActions = (invite: Invite) => (
    <div className="flex justify-end gap-1">
      <RowActions
        inline
        actions={[
          {
            label: t("admin.users.invites.resend"),
            onSelect: () => {
              resend.mutate(invite.id);
            },
            disabled: !smtpConfigured || resend.isPending,
            hidden: invite.status === "accepted",
          },
          {
            label: t("admin.users.invites.revoke"),
            onSelect: () => {
              setRevokeTarget(invite);
            },
            disabled: revoke.isPending,
            hidden: invite.status !== "pending",
          },
        ]}
      />
    </div>
  );

  return (
    <div className="flex flex-col gap-3 pt-3">
      <div className="flex flex-wrap items-center gap-2">
        <SearchInput
          value={list.input}
          onChange={list.setInput}
          onClear={list.clearSearch}
          placeholder={t("admin.users.invites.searchPlaceholder")}
        />
        <FacetSelect
          label={t("admin.users.facets.status")}
          options={(["pending", "accepted", "expired"] as const).map((value) => ({
            value,
            label: t(`admin.users.invites.status.${value}`),
          }))}
          selected={list.facets["status"] ?? []}
          onToggle={(value) => {
            list.toggleFacet("status", value);
          }}
        />
        <FacetSelect
          label={t("admin.users.facets.role")}
          options={ROLES.map((value) => ({
            value,
            label: roleLabel(value, t),
          }))}
          selected={list.facets["role"] ?? []}
          onToggle={(value) => {
            list.toggleFacet("role", value);
          }}
        />
      </div>

      {invites.isPending ? (
        <TableSkeleton cols={4} />
      ) : invites.isError ? (
        <EmptyState
          variant="error"
          onRetry={() => {
            void invites.refetch();
          }}
        />
      ) : invites.data.items.length === 0 ? (
        <EmptyState
          filtered={list.isFiltered}
          onReset={list.clearFilters}
          icon={MailIcon}
          title={t("admin.users.invites.emptyTitle")}
          description={t("admin.users.invites.emptyHint")}
        />
      ) : (
        <>
          <TableFrame>
            <DataTable>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("admin.users.columns.email")}</TableHead>
                  <TableHead>{t("admin.users.columns.role")}</TableHead>
                  <TableHead>{t("admin.users.columns.status")}</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {invites.data.items.map((invite) => (
                  <TableRow key={invite.id} className="hover:bg-muted/40 h-12 align-middle">
                    <TruncateCell className="max-w-[16rem] font-medium" text={invite.email} />
                    <TableCell>{roleLabel(invite.role, t)}</TableCell>
                    <TableCell>
                      <Badge variant={STATUS_TONE[invite.status]}>
                        {t(`admin.users.invites.status.${invite.status}`)}
                      </Badge>
                    </TableCell>
                    <TableCell>{rowActions(invite)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </DataTable>
          </TableFrame>
          <Pagination
            page={invites.data.page}
            perPage={list.perPage}
            total={invites.data.total}
            onPageChange={list.setPage}
            onPerPageChange={list.setPerPage}
          />
        </>
      )}

      <ConfirmDialog
        open={revokeTarget !== null}
        onOpenChange={(open) => {
          if (!open) setRevokeTarget(null);
        }}
        title={t("admin.users.invites.revokeConfirmTitle")}
        description={t("admin.users.invites.revokeConfirmBody", {
          email: revokeTarget?.email ?? "",
        })}
        confirmLabel={t("admin.users.invites.revoke")}
        destructive
        pending={revoke.isPending}
        onConfirm={() => {
          if (revokeTarget) revoke.mutate(revokeTarget.id);
        }}
      />
    </div>
  );
}
