import { LogOutIcon, UserIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { logout, logoutAll } from "@/features/auth/api";
import { roleLabel } from "@/features/auth/roles";
import type { SessionUser } from "@/features/auth/types";
import { initials } from "@/lib/format";

/** Appbar account dropdown (identity + sign-out), shared by the admin and user shells. */
export function AccountMenu({ user }: { user: SessionUser | null }) {
  const { t } = useTranslation();

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        aria-label={t("common.header.account")}
        className="bg-secondary text-secondary-foreground ring-border hover:ring-ring focus-visible:ring-ring flex size-8 cursor-pointer items-center justify-center rounded-full text-xs font-semibold ring-1 transition-shadow outline-none hover:ring-2 focus-visible:ring-2"
      >
        {user ? initials(user.full_name) : "·"}
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-56">
        {user && (
          <div className="px-1.5 py-1.5">
            <p className="text-sm font-medium">{user.full_name}</p>
            <p className="text-muted-foreground text-xs">{user.email}</p>
            <p className="text-muted-foreground/80 mt-0.5 text-xs">{roleLabel(user.role, t)}</p>
          </div>
        )}
        <DropdownMenuSeparator />
        <DropdownMenuItem render={<Link to="/account" />}>
          <UserIcon />
          {t("common.header.account")}
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onClick={() => {
            void logout();
          }}
        >
          <LogOutIcon />
          {t("common.header.signOut")}
        </DropdownMenuItem>
        <DropdownMenuItem
          variant="destructive"
          onClick={() => {
            void logoutAll();
          }}
        >
          <LogOutIcon />
          {t("common.header.signOutAll")}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
