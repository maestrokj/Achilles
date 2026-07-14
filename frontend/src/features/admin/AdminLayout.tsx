import { MessageSquareIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link, NavLink, Outlet } from "react-router-dom";

import { AccountMenu } from "@/components/AccountMenu";
import { BrandMark } from "@/components/BrandMark";
import { HeaderControls } from "@/components/HeaderControls";
import { Button } from "@/components/ui/button";
import { isOwner } from "@/features/auth/roles";
import { useSession } from "@/features/auth/session-context";
import { BellButton } from "@/features/notifications/BellButton";
import { cn } from "@/lib/utils";

import { ADMIN_NAV } from "./nav";

/** Shell of every admin route: appbar + grouped sidebar around the <Outlet/>. */
export function AdminLayout() {
  const { t } = useTranslation();
  const session = useSession();
  const user = session.status === "authenticated" ? session.user : null;
  const role = user?.role ?? "member";

  return (
    <div className="bg-background text-foreground flex h-screen flex-col">
      <header className="border-border bg-card flex h-14 shrink-0 items-center gap-3 border-b px-5">
        <Link to="/admin">
          <BrandMark className="text-sm" />
        </Link>

        <span className="flex-1" />

        <HeaderControls />

        <BellButton inboxPath="/admin/notifications/inbox" settingsPath="/admin/notifications" />

        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={t("common.header.toChat")}
          title={t("common.header.toChat")}
          render={<Link to="/chat" />}
        >
          <MessageSquareIcon />
        </Button>

        <AccountMenu user={user} />
      </header>

      <div className="flex min-h-0 flex-1">
        <aside className="border-sidebar-border bg-sidebar w-60 shrink-0 overflow-y-auto border-r px-3 py-4">
          {ADMIN_NAV.map((group) => (
            <div key={group.labelKey} className="mb-5">
              <div className="text-muted-foreground/70 mb-1.5 px-2 text-xs font-semibold">
                {t(group.labelKey)}
              </div>
              {group.items
                .filter((item) => !item.ownerOnly || isOwner(role))
                .map((item) => (
                  <NavLink
                    key={item.path}
                    to={item.path === "" ? "/admin" : `/admin/${item.path}`}
                    end={item.path === ""}
                    className={({ isActive }) =>
                      cn(
                        "flex h-9 items-center gap-2.5 rounded-lg px-2.5 text-sm transition-colors",
                        isActive
                          ? "bg-sidebar-accent text-sidebar-accent-foreground font-medium"
                          : "text-sidebar-foreground/80 hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground",
                      )
                    }
                  >
                    <item.icon className="size-4 shrink-0" aria-hidden="true" />
                    <span className="min-w-0 flex-1 truncate">{t(item.labelKey)}</span>
                  </NavLink>
                ))}
            </div>
          ))}

          <div className="border-sidebar-border border-t pt-3">
            <Link
              to="/chat"
              className="text-sidebar-foreground/80 hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground flex items-center gap-2.5 rounded-lg px-2.5 py-2 transition-colors"
            >
              <MessageSquareIcon className="size-4 shrink-0" aria-hidden="true" />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm">{t("admin.demoLink.title")}</span>
                <span className="text-sidebar-foreground/50 block truncate text-xs">
                  {t("admin.demoLink.caption")}
                </span>
              </span>
            </Link>
          </div>
        </aside>

        <main className="min-w-0 flex-1 overflow-y-auto">
          <div className="px-8 py-8">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
