import { MoreHorizontalIcon, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

export type RowAction = {
  label: string;
  onSelect: () => void;
  icon?: LucideIcon;
  destructive?: boolean;
  disabled?: boolean;
  /** When true the action is dropped entirely (row-specific gating). */
  hidden?: boolean;
};

/** Canonical trailing "⋯" overflow menu for a table/list row: a single
 * standardized trigger (ghost · icon-sm · MoreHorizontal). Use this directly
 * when the menu body needs bespoke items — sub-menus, radio groups, dividers —
 * that a flat action list can't express. For plain action lists use RowActions. */
export function RowActionsMenu({
  children,
  label,
  align = "end",
}: {
  children: ReactNode;
  label?: string;
  align?: "start" | "center" | "end";
}) {
  const { t } = useTranslation();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button variant="ghost" size="icon-sm" aria-label={label ?? t("common.rowActions")} />
        }
      >
        <MoreHorizontalIcon />
      </DropdownMenuTrigger>
      <DropdownMenuContent align={align}>{children}</DropdownMenuContent>
    </DropdownMenu>
  );
}

/** Row actions, rendered by the project canon:
 * 0 visible → nothing · 1 → a direct ghost button · 2+ → the "⋯" overflow menu.
 * Destructive actions sink below a separator inside the menu.
 *
 * Pass `inline` to keep every action a visible ghost button and never collapse
 * into the menu — for the rare row whose actions must stay on show. */
export function RowActions({
  actions,
  label,
  inline,
}: {
  actions: RowAction[];
  label?: string;
  inline?: boolean;
}) {
  const visible = actions.filter((action) => !action.hidden);
  if (visible.length === 0) return null;

  if (inline || visible.length === 1) {
    return (
      <>
        {visible.map((action) => (
          <RowActionButton key={action.label} action={action} />
        ))}
      </>
    );
  }

  const normal = visible.filter((action) => !action.destructive);
  const destructive = visible.filter((action) => action.destructive);
  return (
    <RowActionsMenu label={label}>
      {normal.map((action) => (
        <RowActionItem key={action.label} action={action} />
      ))}
      {destructive.length > 0 && normal.length > 0 && <DropdownMenuSeparator />}
      {destructive.map((action) => (
        <RowActionItem key={action.label} action={action} />
      ))}
    </RowActionsMenu>
  );
}

function RowActionButton({ action }: { action: RowAction }) {
  const Icon = action.icon;
  return (
    <Button
      variant="ghost"
      size="sm"
      disabled={action.disabled}
      className={cn(action.destructive && "text-destructive hover:text-destructive")}
      onClick={action.onSelect}
    >
      {Icon && <Icon />}
      {action.label}
    </Button>
  );
}

function RowActionItem({ action }: { action: RowAction }) {
  const Icon = action.icon;
  return (
    <DropdownMenuItem
      variant={action.destructive ? "destructive" : "default"}
      disabled={action.disabled}
      onClick={action.onSelect}
    >
      {Icon && <Icon />}
      {action.label}
    </DropdownMenuItem>
  );
}
