import { ChevronDownIcon, ChevronsUpDownIcon, ChevronUpIcon } from "lucide-react";
import * as React from "react";
import { Link, type LinkProps } from "react-router-dom";

import { TruncatedText } from "@/components/TruncatedText";
import { Table, TableCell, TableHead } from "@/components/ui/table";
import { cn } from "@/lib/utils";

// Canonical admin table look — one source of truth for container chrome, header
// typography, edge padding and the calm header row. Screens still compose their
// own TableHeader/Row/Cell inside; sortable columns pair SortableHead with
// useClientSort. admin-panel/_workzone/list-controls.html

/** Header typography + edge padding + quiet (non-hovering) header row, applied
 *  to every th/td via the table element so consumers add nothing per column. */
const TABLE_CANON =
  "[&_thead_tr]:hover:bg-transparent " +
  "[&_th]:text-muted-foreground [&_th]:text-xs [&_th]:font-medium " +
  "[&_td:first-child]:pl-4 [&_td:last-child]:pr-4 " +
  "[&_th:first-child]:pl-4 [&_th:last-child]:pr-4";

/** The bordered shell around a table.
 *  - "panel" (default): standalone card surface — bg-card, rounded-xl, soft shadow.
 *  - "card": sits inside a <Card> that already owns the header/actions — flat, rounded-lg. */
export function TableFrame({
  variant = "panel",
  className,
  children,
}: {
  variant?: "panel" | "card";
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "overflow-hidden border",
        variant === "panel" ? "bg-card rounded-xl shadow-2xs" : "rounded-lg",
        className,
      )}
    >
      {children}
    </div>
  );
}

/** <Table> carrying the canonical admin classes — a drop-in for ui/table's Table. */
export function DataTable({ className, ...props }: React.ComponentProps<typeof Table>) {
  return <Table className={cn(TABLE_CANON, className)} {...props} />;
}

/** A cell for user-authored text that could be arbitrarily long: clips it to one
 *  line with an ellipsis and reveals the full value as a native tooltip, so a
 *  runaway name/email/URL never widens the column (tables are `table-auto`).
 *
 *  Give the cell a width cap through `className` (e.g. `max-w-[16rem]`) — without
 *  one the column still grows. Pass the raw string as `text` (feeds the reveal
 *  and is the fallback content); wrap it in a link/markup via `children` when the
 *  cell links somewhere. Single-line values reveal the full text in a styled
 *  tooltip on hover, but only when actually clipped. For a multi-line value use
 *  `clamp` (2 lines by default). Composite cells (icon + text, two stacked lines)
 *  truncate inline instead — put `min-w-0 truncate` on the text element. */
export function TruncateCell({
  text,
  clamp,
  className,
  children,
}: {
  text: string;
  clamp?: 2 | 3;
  className?: string;
  children?: React.ReactNode;
}) {
  return (
    <TableCell className={cn(clamp && "whitespace-normal", className)}>
      {clamp ? (
        <div title={text} className={clamp === 2 ? "line-clamp-2" : "line-clamp-3"}>
          {children ?? text}
        </div>
      ) : (
        <TruncatedText tooltip={text} render={<div />}>
          {children ?? text}
        </TruncatedText>
      )}
    </TableCell>
  );
}

// ── Whole-row navigation ────────────────────────────────────────────────────
// A row that leads to a detail page becomes one click target — implemented as a
// real stretched <a> (native ⌘/middle-click, "open in new tab", keyboard,
// context menu) rather than a JS onClick, which would forfeit all of those. The
// pattern: mark the <TableRow> with ROW_LINK_ROW, put a <RowLink> in the primary
// cell, and lift any secondary link/menu above the overlay with ROW_LINK_ABOVE.
// admin-panel/_workzone/list-controls.html

/** Class for a <TableRow> that navigates as a whole: it is the positioning
 *  context the <RowLink> overlay stretches within, and carries the pointer
 *  cursor plus the hover tint. Compose with per-table row classes (height,
 *  align) via a template string. */
export const ROW_LINK_ROW = "hover:bg-muted/40 relative cursor-pointer transition-colors";

/** Lifts a cell (or an inline element) above the RowLink overlay so its own
 *  link / menu / popover stays clickable and its text selectable. Put it on the
 *  <TableCell> holding the trailing actions menu or a secondary link. */
export const ROW_LINK_ABOVE = "relative z-10";

/** The primary (name/title) link of a whole-row-clickable table row. Renders a
 *  real router <Link> whose `::before` stretches over the entire row, so a click
 *  anywhere on the row follows it while ⌘/middle-click, "open in new tab" and
 *  keyboard focus stay native. Truncation lives on an inner span so the overlay
 *  is never clipped (a `truncate` on the link itself would clip its `::before`).
 *  The row must carry ROW_LINK_ROW; secondary links/menus use ROW_LINK_ABOVE. */
export function RowLink({
  to,
  children,
  className,
}: {
  to: LinkProps["to"];
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <Link
      to={to}
      className={cn(
        "block min-w-0 font-medium hover:underline",
        "before:absolute before:inset-0 before:rounded-md before:content-['']",
        "focus-visible:before:ring-ring focus-visible:outline-none focus-visible:before:ring-2 focus-visible:before:ring-inset",
        className,
      )}
    >
      <span className="block truncate">{children}</span>
    </Link>
  );
}

export interface SortState<K extends string> {
  key: K;
  desc: boolean;
}

/** A clickable, sort-aware column header. The caret reflects the active column
 *  and its direction; inactive columns show a muted two-way caret. */
export function SortableHead<K extends string>({
  label,
  sortKey,
  sort,
  onToggle,
  align = "start",
  className,
}: {
  label: React.ReactNode;
  sortKey: K;
  sort: SortState<K>;
  onToggle: (key: K) => void;
  align?: "start" | "center" | "end";
  className?: string;
}) {
  const active = sort.key === sortKey;
  const Caret = active ? (sort.desc ? ChevronDownIcon : ChevronUpIcon) : ChevronsUpDownIcon;
  return (
    <TableHead
      className={cn(
        align === "center" && "text-center",
        align === "end" && "text-right",
        className,
      )}
    >
      <button
        type="button"
        className={cn(
          "hover:text-foreground inline-flex items-center gap-1 transition-colors",
          align === "center" && "mx-auto",
          align === "end" && "ml-auto",
          active && "text-foreground",
        )}
        onClick={() => {
          onToggle(sortKey);
        }}
      >
        {label}
        <Caret aria-hidden="true" className={cn("size-3", !active && "opacity-50")} />
      </button>
    </TableHead>
  );
}
