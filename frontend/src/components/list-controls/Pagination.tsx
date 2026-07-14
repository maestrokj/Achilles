import { ChevronLeftIcon, ChevronRightIcon } from "lucide-react";
import { Fragment } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { PER_PAGE_CHOICES, type PerPage } from "./useListState";

/** Numbered pages around the current one: 1 … p-1 p p+1 … last. */
function pageItems(page: number, lastPage: number): (number | "gap")[] {
  const wanted = new Set([1, page - 1, page, page + 1, lastPage]);
  const pages = [...wanted].filter((p) => p >= 1 && p <= lastPage).sort((a, b) => a - b);
  const items: (number | "gap")[] = [];
  let prev = 0;
  for (const p of pages) {
    if (p - prev > 1) items.push("gap");
    items.push(p);
    prev = p;
  }
  return items;
}

export function Pagination({
  page,
  perPage,
  total,
  onPageChange,
  onPerPageChange,
}: {
  page: number;
  perPage: PerPage;
  total: number;
  onPageChange: (page: number) => void;
  onPerPageChange: (perPage: PerPage) => void;
}) {
  const { t } = useTranslation();
  const lastPage = Math.max(1, Math.ceil(total / perPage));
  const from = total === 0 ? 0 : (page - 1) * perPage + 1;
  const to = Math.min(page * perPage, total);

  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <span className="text-muted-foreground text-sm">
        {t("common.list.range", { from, to, total })}
      </span>
      <div className="flex items-center gap-1">
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={t("common.list.prevPage")}
          disabled={page <= 1}
          onClick={() => {
            onPageChange(page - 1);
          }}
        >
          <ChevronLeftIcon className="size-4" />
        </Button>
        {pageItems(page, lastPage).map((item, index) =>
          item === "gap" ? (
            <Fragment key={`gap-${String(index)}`}>
              <span className="text-muted-foreground px-1 text-sm">…</span>
            </Fragment>
          ) : (
            <Button
              key={item}
              variant={item === page ? "outline" : "ghost"}
              size="sm"
              className="min-w-7"
              onClick={() => {
                onPageChange(item);
              }}
            >
              {item}
            </Button>
          ),
        )}
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={t("common.list.nextPage")}
          disabled={page >= lastPage}
          onClick={() => {
            onPageChange(page + 1);
          }}
        >
          <ChevronRightIcon className="size-4" />
        </Button>
      </div>
      <Select
        items={PER_PAGE_CHOICES.map((choice) => ({
          value: String(choice),
          label: t("common.list.perPage", { count: choice }),
        }))}
        value={String(perPage)}
        onValueChange={(value) => {
          onPerPageChange(Number(value) as PerPage);
        }}
      >
        <SelectTrigger size="sm" className="w-40">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {PER_PAGE_CHOICES.map((choice) => (
            <SelectItem key={choice} value={String(choice)}>
              {t("common.list.perPage", { count: choice })}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
