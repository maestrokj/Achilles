import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { LinkIcon, PencilIcon, PinIcon, PlusIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { TruncatedText } from "@/components/TruncatedText";
import { DataTable, TableFrame } from "@/components/list-controls/DataTable";
import { EmptyState } from "@/components/list-controls/EmptyState";
import { FacetSelect } from "@/components/list-controls/FacetSelect";
import { Pagination } from "@/components/list-controls/Pagination";
import { SearchInput } from "@/components/list-controls/SearchInput";
import { TableSkeleton } from "@/components/list-controls/TableSkeleton";
import { buildListQuery, useListState } from "@/components/list-controls/useListState";
import { Button } from "@/components/ui/button";
import { TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { initials } from "@/lib/format";

import { mappingMatrix, usersKeys } from "./api";
import { IdentityLinkPopover } from "./IdentityLinkPopover";
import type { MappingRow, MappingSource } from "./types";

const FACETS = ["source_id", "link_status"] as const;

/** Left divider on every source column — turns the wide matrix into a scannable grid
 * instead of a wall of cells. The first (User) column stays flush. */
const SOURCE_COL = "border-border/60 border-l pl-3";

export function IdentityMappingTab() {
  const { t } = useTranslation();
  const list = useListState(FACETS);
  const query = buildListQuery(list);
  const matrix = useQuery({
    queryKey: usersKeys.mapping(query),
    queryFn: () => mappingMatrix(query),
    placeholderData: keepPreviousData,
  });

  // The facet always lists every source (the backend returns all); the selection
  // narrows which source columns the matrix renders, so the filter has a visible
  // effect on its own and reads as "… on this source" alongside the status facet.
  const allSources = matrix.data?.sources ?? [];
  const picked = list.facets["source_id"] ?? [];
  const shownSources =
    picked.length === 0
      ? allSources
      : allSources.filter((source) => picked.includes(String(source.id)));

  return (
    <div className="flex flex-col gap-3 pt-3">
      <p className="text-muted-foreground max-w-3xl text-sm">{t("admin.users.identity.intro")}</p>
      <div className="flex flex-wrap items-center gap-2">
        <SearchInput
          value={list.input}
          onChange={list.setInput}
          onClear={list.clearSearch}
          placeholder={t("admin.users.identity.searchPlaceholder")}
        />
        <FacetSelect
          label={t("admin.users.identity.source")}
          preserveOptionCase
          options={allSources.map((source) => ({
            value: String(source.id),
            label: source.name,
          }))}
          selected={picked}
          onToggle={(value) => {
            list.toggleFacet("source_id", value);
          }}
        />
        <FacetSelect
          label={t("admin.users.identity.linkStatus")}
          options={(["matched", "unmatched", "manual"] as const).map((value) => ({
            value,
            label: t(`admin.users.identity.statuses.${value}`),
          }))}
          selected={list.facets["link_status"] ?? []}
          onToggle={(value) => {
            list.toggleFacet("link_status", value);
          }}
        />
      </div>

      {matrix.isPending ? (
        <TableSkeleton cols={4} />
      ) : matrix.isError ? (
        <EmptyState
          variant="error"
          onRetry={() => {
            void matrix.refetch();
          }}
        />
      ) : matrix.data.items.length === 0 ? (
        <EmptyState
          filtered={list.isFiltered}
          onReset={list.clearFilters}
          icon={LinkIcon}
          title={t("admin.users.identity.emptyTitle")}
          description={t("admin.users.identity.emptyHint")}
        />
      ) : (
        <>
          <TableFrame>
            <DataTable>
              <TableHeader className="[&_th]:h-11">
                <TableRow>
                  <TableHead className="min-w-52">{t("admin.users.identity.user")}</TableHead>
                  {shownSources.map((source) => (
                    <TableHead key={source.id} className={`min-w-40 ${SOURCE_COL}`}>
                      <span className="flex items-center gap-2">
                        <SourceMonogram name={source.name} />
                        <TruncatedText className="text-foreground font-medium">
                          {source.name}
                        </TruncatedText>
                      </span>
                    </TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {matrix.data.items.map((row) => (
                  <TableRow key={row.user_id} className="hover:bg-muted/40 h-14 align-middle">
                    <TableCell className="max-w-[16rem]">
                      <span className="flex items-center gap-2.5">
                        <span className="bg-secondary text-secondary-foreground flex size-8 shrink-0 items-center justify-center rounded-full text-xs font-semibold">
                          {initials(row.full_name)}
                        </span>
                        <span className="flex min-w-0 flex-col">
                          <TruncatedText
                            render={
                              <Link
                                to={`/admin/users/${String(row.user_id)}`}
                                className="font-medium hover:underline"
                              />
                            }
                          >
                            {row.full_name}
                          </TruncatedText>
                          <TruncatedText className="text-muted-foreground text-xs">
                            {row.email}
                          </TruncatedText>
                        </span>
                      </span>
                    </TableCell>
                    {shownSources.map((source) => (
                      <MappingCell key={source.id} row={row} source={source} />
                    ))}
                  </TableRow>
                ))}
              </TableBody>
            </DataTable>
          </TableFrame>
          <Pagination
            page={matrix.data.page}
            perPage={list.perPage}
            total={matrix.data.total}
            onPageChange={list.setPage}
            onPerPageChange={list.setPerPage}
          />
        </>
      )}
    </div>
  );
}

/** Neutral initials tile for a source column header — a visual anchor so six
 * columns scan at a glance, without pulling in per-vendor brand assets. */
function SourceMonogram({ name }: { name: string }) {
  return (
    <span className="bg-muted text-muted-foreground border-border/60 flex size-6 shrink-0 items-center justify-center rounded-md border text-[0.65rem] font-semibold">
      {initials(name)}
    </span>
  );
}

/** One user × source cell (users.html#identity-mapping, legend 2). The signal — a
 * linked account — is a solid chip that reads first; an empty cell is a quiet dashed
 * "+" slot, so the eye lands on what's linked rather than on the actions. Every state
 * opens the same popover: a linked chip carries a pencil (override a confident
 * auto-match) and a pin marks a manual choice; "Unlink" lives inside the popover. */
function MappingCell({ row, source }: { row: MappingRow; source: MappingSource }) {
  const { t } = useTranslation();
  const links = row.links.filter((link) => link.source_id === source.id);
  if (links.length === 0) {
    // The whole empty cell is the link target: the trigger button fills it edge to
    // edge (cell padding moves onto the button), so a click anywhere opens the
    // popover and the cursor reads as a hand. The dashed "+" chip stays a quiet slot
    // that lights up on cell hover via the button's own group.
    return (
      <TableCell className={`${SOURCE_COL} p-0`}>
        <IdentityLinkPopover
          userId={row.user_id}
          source={source}
          trigger={
            <Button
              variant="ghost"
              aria-label={t("admin.users.identity.linkAria", { source: source.name })}
              title={t("admin.users.identity.linkAria", { source: source.name })}
              className="h-14 w-full justify-start rounded-none py-2 pr-2 pl-3 hover:bg-transparent aria-expanded:bg-transparent"
            >
              <span className="text-muted-foreground/70 border-border/70 group-hover/button:text-foreground group-hover/button:border-border group-hover/button:bg-muted flex size-6 shrink-0 items-center justify-center rounded-md border border-dashed transition-colors">
                <PlusIcon className="size-3" />
              </span>
            </Button>
          }
        />
      </TableCell>
    );
  }
  return (
    <TableCell className={SOURCE_COL}>
      <span className="flex flex-col items-start gap-1">
        {links.map((link) => (
          <IdentityLinkPopover
            key={link.principal_id}
            userId={row.user_id}
            source={source}
            current={link}
            trigger={
              <Button
                variant="ghost"
                size="xs"
                className="group border-border bg-secondary/50 text-foreground hover:bg-secondary hover:border-border max-w-full gap-1.5 rounded-md border font-normal"
              >
                {link.pinned && (
                  <PinIcon
                    className="text-muted-foreground size-3 shrink-0"
                    aria-label={t("admin.users.identity.pinned")}
                  />
                )}
                <span className="truncate">
                  {link.display_name ?? link.email ?? link.source_user_id}
                </span>
                <PencilIcon className="text-muted-foreground ml-auto size-3 shrink-0 opacity-0 transition-opacity group-hover:opacity-100" />
              </Button>
            }
          />
        ))}
      </span>
    </TableCell>
  );
}
