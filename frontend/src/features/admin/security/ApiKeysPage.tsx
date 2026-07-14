import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyIcon } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { toastApiError } from "@/api/errors";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { TruncatedText } from "@/components/TruncatedText";
import { DataTable, TableFrame, TruncateCell } from "@/components/list-controls/DataTable";
import { EmptyState } from "@/components/list-controls/EmptyState";
import { FacetSelect } from "@/components/list-controls/FacetSelect";
import { Pagination } from "@/components/list-controls/Pagination";
import { RowActions } from "@/components/list-controls/RowActions";
import { SearchInput } from "@/components/list-controls/SearchInput";
import { TableSkeleton } from "@/components/list-controls/TableSkeleton";
import { buildListQuery, useListState } from "@/components/list-controls/useListState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { revokeKey } from "@/features/admin/users/api";
import { formatWhen } from "@/lib/format";

import { listCompanyKeys, securityKeys } from "./api";
import { CreateKeyDialog } from "./CreateKeyDialog";

import { API_KEY_STATUSES, type AdminApiKey } from "./types";

const FACETS = ["status"] as const;

const STATUS_TONE: Record<AdminApiKey["status"], "success" | "warning" | "destructive"> = {
  active: "success",
  expired: "warning",
  revoked: "destructive",
};

/** Admin · API Keys: every machine key in the company, revoke any.
 * Wireframe: admin-panel/_wireframes/api-keys.html. */
export function ApiKeysPage() {
  const { t, i18n } = useTranslation();
  const list = useListState(FACETS);
  const queryClient = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [revokeTarget, setRevokeTarget] = useState<AdminApiKey | null>(null);
  const query = buildListQuery(list);
  const keys = useQuery({
    queryKey: securityKeys.companyKeys(query),
    queryFn: () => listCompanyKeys(query),
    placeholderData: keepPreviousData,
  });
  const revoke = useMutation({
    mutationFn: revokeKey,
    onSuccess: () => {
      setRevokeTarget(null);
      void queryClient.invalidateQueries({ queryKey: ["admin", "api-keys"] });
    },
    onError: (error) => void toastApiError(error, t("admin.users.card.actionFailed")),
  });

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">{t("admin.nav.apiKeys")}</h1>
        <Button
          size="sm"
          onClick={() => {
            setCreateOpen(true);
          }}
        >
          {t("admin.apiKeys.create")}
        </Button>
      </div>
      <CreateKeyDialog open={createOpen} onOpenChange={setCreateOpen} />
      <div className="flex flex-wrap items-center gap-2">
        <SearchInput
          value={list.input}
          onChange={list.setInput}
          onClear={list.clearSearch}
          placeholder={t("admin.apiKeys.searchPlaceholder")}
        />
        <FacetSelect
          label={t("admin.users.facets.status")}
          options={API_KEY_STATUSES.map((value) => ({
            value,
            label: t(`admin.apiKeys.status.${value}`),
          }))}
          selected={list.facets["status"] ?? []}
          onToggle={(value) => {
            list.toggleFacet("status", value);
          }}
        />
      </div>

      {keys.isPending ? (
        <TableSkeleton cols={7} />
      ) : keys.isError ? (
        <EmptyState
          variant="error"
          onRetry={() => {
            void keys.refetch();
          }}
        />
      ) : keys.data.items.length === 0 ? (
        <EmptyState
          filtered={list.isFiltered}
          onReset={list.clearFilters}
          icon={KeyIcon}
          title={t("admin.apiKeys.emptyTitle")}
          description={t("admin.apiKeys.emptyHint")}
        />
      ) : (
        <>
          <TableFrame>
            <DataTable>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("admin.apiKeys.columns.key")}</TableHead>
                  <TableHead>{t("admin.apiKeys.columns.owner")}</TableHead>
                  <TableHead>{t("admin.apiKeys.columns.sources")}</TableHead>
                  <TableHead>{t("admin.apiKeys.columns.status")}</TableHead>
                  <TableHead>{t("admin.apiKeys.columns.expires")}</TableHead>
                  <TableHead>{t("admin.apiKeys.columns.lastUsed")}</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {keys.data.items.map((key) => {
                  return (
                    <TableRow key={key.id} className="hover:bg-muted/40 h-12">
                      <TableCell className="max-w-[14rem]">
                        {key.name ? (
                          <div className="flex flex-col">
                            <TruncatedText className="text-sm">{key.name}</TruncatedText>
                            <code className="text-muted-foreground text-xs">{key.prefix}…</code>
                          </div>
                        ) : (
                          <code className="text-xs">{key.prefix}…</code>
                        )}
                      </TableCell>
                      <TableCell className="max-w-[16rem]">
                        <TruncatedText
                          render={
                            <Link
                              to={`/admin/users/${String(key.owner.id)}`}
                              className="hover:underline"
                            />
                          }
                        >
                          {key.owner.full_name}
                        </TruncatedText>
                        <TruncatedText className="text-muted-foreground text-xs">
                          {key.owner.email}
                        </TruncatedText>
                      </TableCell>
                      <TruncateCell
                        className="text-muted-foreground max-w-[16rem] text-sm tabular-nums"
                        text={
                          key.scope.sources === null
                            ? t("admin.apiKeys.allSources")
                            : String(key.scope.sources.length)
                        }
                      />

                      <TableCell>
                        <Badge variant={STATUS_TONE[key.status]}>
                          {t(`admin.apiKeys.status.${key.status}`)}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm tabular-nums">
                        {key.expires_at
                          ? formatWhen(key.expires_at, i18n.language)
                          : t("admin.apiKeys.noExpiry")}
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm tabular-nums">
                        {formatWhen(key.last_used_at, i18n.language) ?? "—"}
                      </TableCell>
                      <TableCell className="text-right">
                        <RowActions
                          actions={[
                            {
                              label: t("admin.users.card.revoke"),
                              onSelect: () => {
                                setRevokeTarget(key);
                              },
                              hidden: key.status === "revoked",
                            },
                          ]}
                        />
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </DataTable>
          </TableFrame>
          <Pagination
            page={keys.data.page}
            perPage={list.perPage}
            total={keys.data.total}
            onPageChange={list.setPage}
            onPerPageChange={list.setPerPage}
          />
        </>
      )}
      <ConfirmDialog
        open={revokeTarget !== null}
        onOpenChange={(next) => {
          if (!next) setRevokeTarget(null);
        }}
        title={t("admin.apiKeys.revokeTitle")}
        description={t("admin.apiKeys.revokeDescription", {
          prefix: revokeTarget?.prefix ?? "",
          owner: revokeTarget?.owner.full_name ?? "",
        })}
        confirmLabel={t("admin.users.card.revoke")}
        destructive
        pending={revoke.isPending}
        onConfirm={() => {
          if (revokeTarget) revoke.mutate(revokeTarget.id);
        }}
      />
    </div>
  );
}
