import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { UsersIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  DataTable,
  ROW_LINK_ABOVE,
  ROW_LINK_ROW,
  RowLink,
  TableFrame,
  TruncateCell,
} from "@/components/list-controls/DataTable";
import { EmptyState } from "@/components/list-controls/EmptyState";
import { FacetSelect } from "@/components/list-controls/FacetSelect";
import { Pagination } from "@/components/list-controls/Pagination";
import { SearchInput } from "@/components/list-controls/SearchInput";
import { TableSkeleton } from "@/components/list-controls/TableSkeleton";
import { buildListQuery, useListState } from "@/components/list-controls/useListState";
import { Badge } from "@/components/ui/badge";
import { TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { userStatusBadgeVariant } from "@/lib/badges";
import { formatWhen } from "@/lib/format";

import { listUsers, usersKeys } from "./api";
import { ExportMenu } from "./ExportMenu";
import { ROLES, roleLabel } from "./format";
import { UserRowMenu } from "./UserRowMenu";

const FACETS = ["role", "status", "last_login"] as const;

/** Windows the backend accepts for the `last_login` filter (admin_users.py). */
const LAST_LOGIN_OPTIONS = ["24h", "7d", "30d", "never"] as const;

export function UsersListTab() {
  const { t, i18n } = useTranslation();
  const list = useListState(FACETS);
  const query = buildListQuery(list);
  const users = useQuery({
    queryKey: usersKeys.list(query),
    queryFn: () => listUsers(query),
    placeholderData: keepPreviousData,
  });

  return (
    <div className="flex flex-col gap-3 pt-3">
      <div className="flex flex-wrap items-center gap-2">
        <SearchInput
          value={list.input}
          onChange={list.setInput}
          onClear={list.clearSearch}
          placeholder={t("admin.users.searchPlaceholder")}
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
        <FacetSelect
          label={t("admin.users.facets.status")}
          options={[
            { value: "active", label: t("admin.users.statuses.active") },
            { value: "deactivated", label: t("admin.users.statuses.deactivated") },
          ]}
          selected={list.facets["status"] ?? []}
          onToggle={(value) => {
            list.toggleFacet("status", value);
          }}
        />
        <FacetSelect
          label={t("admin.users.facets.lastLogin")}
          options={LAST_LOGIN_OPTIONS.map((value) => ({
            value,
            label: t(`admin.users.lastLoginOptions.${value}`),
          }))}
          selected={list.facets["last_login"] ?? []}
          onToggle={(value) => {
            // The backend takes a single window — picking one drops the other.
            for (const prev of list.facets["last_login"] ?? []) {
              if (prev !== value) list.toggleFacet("last_login", prev);
            }
            list.toggleFacet("last_login", value);
          }}
        />
        <div className="ml-auto">
          <ExportMenu query={query} />
        </div>
      </div>

      {users.isPending ? (
        <TableSkeleton cols={6} />
      ) : users.isError ? (
        <EmptyState
          variant="error"
          onRetry={() => {
            void users.refetch();
          }}
        />
      ) : users.data.items.length === 0 ? (
        <EmptyState
          filtered={list.isFiltered}
          onReset={list.clearFilters}
          icon={UsersIcon}
          title={t("admin.users.emptyTitle")}
          description={t("admin.users.emptyHint")}
        />
      ) : (
        <>
          <TableFrame>
            <DataTable>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("admin.users.columns.name")}</TableHead>
                  <TableHead>{t("admin.users.columns.email")}</TableHead>
                  <TableHead>{t("admin.users.columns.role")}</TableHead>
                  <TableHead>{t("admin.users.columns.status")}</TableHead>
                  <TableHead>{t("admin.users.columns.lastLogin")}</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {users.data.items.map((user) => (
                  <TableRow key={user.id} className={`${ROW_LINK_ROW} h-12 align-middle`}>
                    <TableCell className="max-w-[16rem]">
                      <RowLink to={`/admin/users/${String(user.id)}`}>{user.full_name}</RowLink>
                    </TableCell>
                    <TruncateCell
                      className="text-muted-foreground max-w-[16rem]"
                      text={user.email}
                    />
                    <TableCell>{roleLabel(user.role, t)}</TableCell>
                    <TableCell>
                      <Badge variant={userStatusBadgeVariant(user.status)}>
                        {user.status === "active"
                          ? t("admin.users.statuses.active")
                          : t("admin.users.statuses.deactivated")}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground text-sm">
                      {formatWhen(user.last_login_at, i18n.language) ?? t("admin.users.never")}
                    </TableCell>
                    <TableCell className={`${ROW_LINK_ABOVE} text-right`}>
                      <UserRowMenu user={user} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </DataTable>
          </TableFrame>
          <Pagination
            page={users.data.page}
            perPage={list.perPage}
            total={users.data.total}
            onPageChange={list.setPage}
            onPerPageChange={list.setPerPage}
          />
        </>
      )}
    </div>
  );
}
