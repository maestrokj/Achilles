/** Claude-style user-shell sidebar: new chat + primary nav on top, the
 * conversation history below, the account at the bottom. Desktop collapses to
 * an icon rail; small screens turn it into an overlay drawer. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BellIcon,
  BotIcon,
  LogOutIcon,
  MoonIcon,
  MoreHorizontalIcon,
  PanelLeftIcon,
  PencilIcon,
  PlusIcon,
  SettingsIcon,
  ShieldIcon,
  SunIcon,
  Trash2Icon,
  UserIcon,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";

import { toastApiError } from "@/api/errors";
import { BrandMark } from "@/components/BrandMark";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { TruncatedText } from "@/components/TruncatedText";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  chatQueryKeys,
  deleteConversation,
  listConversations,
  renameConversation,
} from "@/features/chat/api";
import type { ConversationListItem } from "@/features/chat/types";
import { logout, logoutAll } from "@/features/auth/api";
import { canAccessAdmin } from "@/features/auth/roles";
import type { SessionUser } from "@/features/auth/types";
import { useEventStream } from "@/features/live/useEventStream";
import { useUnreadCount } from "@/features/notifications/useUnreadCount";
import { currentLocale, setLocale } from "@/i18n";
import { initials } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useTheme } from "@/providers/theme-context";

const COLLAPSED_STORAGE_KEY = "achilles.sidebar.collapsed";
/** Renames share the autogen cap (query_engine/constants.py TITLE_MAX_CHARS). */
const TITLE_MAX_CHARS = 60;

function readStoredCollapsed(): boolean {
  // Small screens start closed; desktop remembers the user's choice.
  if (window.matchMedia("(max-width: 767px)").matches) return true;
  return localStorage.getItem(COLLAPSED_STORAGE_KEY) === "1";
}

export function Sidebar({ user }: { user: SessionUser }) {
  const { t } = useTranslation();
  const [collapsed, setCollapsed] = useState(readStoredCollapsed);

  const toggle = () => {
    setCollapsed((current) => {
      localStorage.setItem(COLLAPSED_STORAGE_KEY, current ? "0" : "1");
      return !current;
    });
  };

  return (
    <>
      {/* Mobile: floating opener when the drawer is closed. */}
      <Button
        variant="outline"
        size="icon-sm"
        aria-label={t("common.sidebar.expand")}
        onClick={toggle}
        className={cn("fixed top-3 left-3 z-30 shadow-sm md:hidden", !collapsed && "hidden")}
      >
        <PanelLeftIcon />
      </Button>
      {!collapsed && (
        <div
          className="fixed inset-0 z-30 bg-black/40 md:hidden"
          aria-hidden="true"
          onClick={toggle}
        />
      )}

      <aside
        className={cn(
          "bg-sidebar text-sidebar-foreground border-sidebar-border flex flex-col border-r",
          "fixed inset-y-0 left-0 z-40 w-72 transition-transform duration-200 md:static md:z-auto md:transition-[width]",
          collapsed ? "-translate-x-full md:w-14 md:translate-x-0" : "translate-x-0 md:w-72",
        )}
      >
        <div className={cn("flex items-center gap-1 p-3", collapsed && "md:flex-col md:gap-2")}>
          <Link to="/chat" className="min-w-0 flex-1" aria-label={t("chat.title")}>
            <BrandMark compact={collapsed} className="truncate text-sm" />
          </Link>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={t(collapsed ? "common.sidebar.expand" : "common.sidebar.collapse")}
            title={t(collapsed ? "common.sidebar.expand" : "common.sidebar.collapse")}
            onClick={toggle}
            className="text-sidebar-foreground/70"
          >
            <PanelLeftIcon />
          </Button>
        </div>

        <nav className={cn("flex flex-col gap-0.5 px-2", collapsed && "md:items-center")}>
          <NavItem to="/chat" end collapsed={collapsed} label={t("chat.newChat")} emphasis>
            <span className="bg-primary text-primary-foreground flex size-5 shrink-0 items-center justify-center rounded-full">
              <PlusIcon className="size-3.5" />
            </span>
          </NavItem>
          <NavItem to="/agents" collapsed={collapsed} label={t("agents.title")}>
            <BotIcon className="size-4 shrink-0" />
          </NavItem>
          <InboxItem collapsed={collapsed} />
        </nav>

        {!collapsed && <History />}
        {collapsed && <div className="flex-1" />}

        <SidebarAccount user={user} collapsed={collapsed} />
      </aside>
    </>
  );
}

function NavItem({
  to,
  end = false,
  collapsed,
  label,
  emphasis = false,
  badge,
  children,
}: {
  to: string;
  end?: boolean;
  collapsed: boolean;
  label: string;
  emphasis?: boolean;
  badge?: ReactNode;
  children: ReactNode;
}) {
  return (
    <NavLink
      to={to}
      end={end}
      aria-label={label}
      title={label}
      className={({ isActive }) =>
        cn(
          "flex h-9 items-center gap-2.5 rounded-lg px-2 text-sm transition-colors",
          collapsed && "md:w-9 md:justify-center md:px-0",
          emphasis && "text-primary font-medium",
          isActive && !emphasis
            ? "bg-sidebar-accent text-sidebar-accent-foreground font-medium"
            : "hover:bg-sidebar-accent/60",
        )
      }
    >
      {children}
      <span className={cn("min-w-0 flex-1 truncate", collapsed && "md:hidden")}>{label}</span>
      {badge}
    </NavLink>
  );
}

function InboxItem({ collapsed }: { collapsed: boolean }) {
  const { t } = useTranslation();
  useEventStream();
  const unread = useUnreadCount();
  const count = unread.data?.count ?? 0;

  const badge =
    count > 0 ? (
      <span
        className={cn(
          "bg-primary text-primary-foreground flex h-4.5 min-w-4.5 items-center justify-center rounded-full px-1 text-[10px] font-semibold",
          collapsed && "md:absolute md:top-0.5 md:right-0.5 md:h-4 md:min-w-4",
        )}
      >
        {count > 99 ? "99+" : count}
      </span>
    ) : undefined;

  return (
    <span className={cn("contents", collapsed && "md:relative md:block")}>
      <NavItem to="/inbox" collapsed={collapsed} label={t("notifications.title")} badge={badge}>
        <BellIcon className="size-4 shrink-0" />
      </NavItem>
    </span>
  );
}

/** The scrolling history block: label + rows; hidden entirely on the rail. */
function History() {
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [renameTarget, setRenameTarget] = useState<ConversationListItem | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ConversationListItem | null>(null);

  const query = useQuery({
    queryKey: chatQueryKeys.conversations,
    queryFn: listConversations,
    staleTime: 30_000,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: chatQueryKeys.conversations });

  const rename = useMutation({
    mutationFn: ({ id, title }: { id: number; title: string }) => renameConversation(id, title),
    onSuccess: invalidate,
    onError: (error) => void toastApiError(error, t("common.sidebar.renameError")),
  });
  const remove = useMutation({
    mutationFn: (id: number) => deleteConversation(id),
    onSuccess: (_data, id) => {
      void invalidate();
      if (location.pathname === `/chat/${String(id)}`) void navigate("/chat");
    },
    onError: (error) => void toastApiError(error, t("common.sidebar.deleteError")),
  });

  return (
    <div className="mt-4 flex min-h-0 flex-1 flex-col">
      <p className="text-sidebar-foreground/60 px-4 pb-1.5 text-xs font-semibold">
        {t("common.sidebar.chats")}
      </p>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        {query.isPending && (
          <div className="flex flex-col gap-1.5 px-2 pt-1">
            <Skeleton className="bg-sidebar-accent h-6" />
            <Skeleton className="bg-sidebar-accent h-6" />
            <Skeleton className="bg-sidebar-accent h-6" />
          </div>
        )}
        {query.isError && (
          <p className="text-muted-foreground px-2 pt-1 text-xs">{t("common.list.error")}</p>
        )}
        {query.data?.items.length === 0 && (
          <p className="text-muted-foreground px-2 pt-1 text-xs">{t("common.sidebar.noChats")}</p>
        )}
        {query.data?.items.map((item) => (
          <HistoryRow
            key={item.id}
            item={item}
            onRename={() => {
              setRenameTarget(item);
            }}
            onDelete={() => {
              setDeleteTarget(item);
            }}
          />
        ))}
      </div>

      <RenameDialog
        key={renameTarget?.id ?? -1}
        target={renameTarget}
        pending={rename.isPending}
        onClose={() => {
          setRenameTarget(null);
        }}
        onSubmit={(title) => {
          if (renameTarget) rename.mutate({ id: renameTarget.id, title });
          setRenameTarget(null);
        }}
      />
      <DeleteDialog
        target={deleteTarget}
        onClose={() => {
          setDeleteTarget(null);
        }}
        onConfirm={() => {
          if (deleteTarget) remove.mutate(deleteTarget.id);
          setDeleteTarget(null);
        }}
      />
    </div>
  );
}

function HistoryRow({
  item,
  onRename,
  onDelete,
}: {
  item: ConversationListItem;
  onRename: () => void;
  onDelete: () => void;
}) {
  const { t } = useTranslation();
  const title = item.title?.trim() || t("common.sidebar.untitled");

  return (
    <div className="group/row relative">
      <NavLink
        to={`/chat/${String(item.id)}`}
        className={({ isActive }) =>
          cn(
            "flex h-8 items-center rounded-lg py-1.5 pr-8 pl-2 text-sm leading-5 transition-colors",
            isActive
              ? "bg-sidebar-accent text-sidebar-accent-foreground"
              : "text-sidebar-foreground/85 hover:bg-sidebar-accent/60",
          )
        }
      >
        <TruncatedText plain className="flex-1">
          {title}
        </TruncatedText>
      </NavLink>

      <DropdownMenu>
        <DropdownMenuTrigger
          render={
            <Button
              variant="ghost"
              size="icon-xs"
              aria-label={t("common.sidebar.rowMenu")}
              className="text-sidebar-foreground/60 absolute top-1 right-1 opacity-0 group-hover/row:opacity-100 focus-visible:opacity-100 aria-expanded:opacity-100"
            />
          }
        >
          <MoreHorizontalIcon />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="min-w-44">
          <DropdownMenuItem onClick={onRename}>
            <PencilIcon />
            {t("common.sidebar.rename")}
          </DropdownMenuItem>
          <DropdownMenuItem variant="destructive" onClick={onDelete}>
            <Trash2Icon />
            {t("common.sidebar.delete")}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}

function RenameDialog({
  target,
  pending,
  onClose,
  onSubmit,
}: {
  target: ConversationListItem | null;
  pending: boolean;
  onClose: () => void;
  onSubmit: (title: string) => void;
}) {
  const { t } = useTranslation();
  // The dialog is keyed by conversation id, so the draft resets per target.
  const [draft, setDraft] = useState(target?.title ?? "");

  return (
    <Dialog
      open={target !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>{t("common.sidebar.renameTitle")}</DialogTitle>
        </DialogHeader>
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            const title = draft.trim();
            if (title) onSubmit(title);
          }}
        >
          <Input
            value={draft}
            maxLength={TITLE_MAX_CHARS}
            onChange={(event) => {
              setDraft(event.target.value);
            }}
          />
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={onClose}>
              {t("common.cancel")}
            </Button>
            <Button type="submit" disabled={pending || draft.trim() === ""}>
              {t("common.sidebar.renameSave")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function DeleteDialog({
  target,
  onClose,
  onConfirm,
}: {
  target: ConversationListItem | null;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const { t } = useTranslation();

  return (
    <AlertDialog
      open={target !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{t("common.sidebar.deleteTitle")}</AlertDialogTitle>
          <AlertDialogDescription>
            {t("common.sidebar.deleteBody", {
              title: target?.title?.trim() || t("common.sidebar.untitled"),
            })}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>{t("common.cancel")}</AlertDialogCancel>
          <AlertDialogAction variant="destructive" onClick={onConfirm}>
            {t("common.sidebar.deleteConfirm")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function SidebarAccount({ user, collapsed }: { user: SessionUser; collapsed: boolean }) {
  const { t } = useTranslation();
  const { resolvedTheme, setTheme } = useTheme();
  const isAdmin = canAccessAdmin(user.role);

  return (
    <div className="border-sidebar-border border-t p-2">
      <DropdownMenu>
        <DropdownMenuTrigger
          aria-label={t("common.header.account")}
          className={cn(
            "hover:bg-sidebar-accent/60 aria-expanded:bg-sidebar-accent flex w-full items-center gap-2.5 rounded-lg p-2 text-left outline-none",
            collapsed && "md:justify-center md:p-1.5",
          )}
        >
          <span className="bg-secondary text-secondary-foreground flex size-8 shrink-0 items-center justify-center rounded-full text-xs font-semibold">
            {initials(user.full_name)}
          </span>
          <span className={cn("min-w-0 flex-1", collapsed && "md:hidden")}>
            <TruncatedText plain className="text-sm font-medium">
              {user.full_name}
            </TruncatedText>
            <TruncatedText plain className="text-sidebar-foreground/60 text-xs">
              {user.email}
            </TruncatedText>
          </span>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" side="top" className="min-w-60">
          <div className="px-1.5 py-1.5">
            <p className="text-sm font-medium">{user.full_name}</p>
            <p className="text-muted-foreground text-xs">{user.email}</p>
          </div>
          <DropdownMenuSeparator />
          <DropdownMenuItem render={<Link to="/account" />}>
            <UserIcon />
            {t("common.header.account")}
          </DropdownMenuItem>
          <DropdownMenuItem render={<Link to="/inbox/settings" />}>
            <SettingsIcon />
            {t("common.header.settings")}
          </DropdownMenuItem>
          {isAdmin && (
            <DropdownMenuItem render={<Link to="/admin" />}>
              <ShieldIcon />
              {t("common.sidebar.adminPanel")}
            </DropdownMenuItem>
          )}
          <DropdownMenuSeparator />
          <DropdownMenuItem
            onClick={() => {
              setTheme(resolvedTheme === "dark" ? "light" : "dark");
            }}
          >
            {resolvedTheme === "dark" ? <SunIcon /> : <MoonIcon />}
            {t("common.header.toggleTheme")}
          </DropdownMenuItem>
          <DropdownMenuItem
            onClick={() => {
              setLocale(currentLocale() === "ru" ? "en" : "ru", user.id);
            }}
          >
            <span className="text-muted-foreground w-4 text-center text-[10px] font-bold">
              {currentLocale().toUpperCase()}
            </span>
            {t("common.header.language")}
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
    </div>
  );
}
