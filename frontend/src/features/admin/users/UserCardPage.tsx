import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  KeyRoundIcon,
  Link2Icon,
  MailIcon,
  MonitorSmartphoneIcon,
  PencilIcon,
  SettingsIcon,
  UserRoundIcon,
} from "lucide-react";
import { type ComponentType, type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "@/lib/toast";

import { toastApiError } from "@/api/errors";
import { BackLink } from "@/components/BackLink";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { PageError } from "@/components/PageError";
import { PageSkeleton } from "@/components/PageSkeleton";
import { Skeleton } from "@/components/ui/skeleton";
import { CreateKeyDialog } from "@/features/admin/security/CreateKeyDialog";
import type { ApiKey } from "@/features/auth/api-keys";
import { isOwner as isOwnerRole, isMember } from "@/features/auth/roles";
import { useSession } from "@/features/auth/session-context";
import { userStatusBadgeVariant } from "@/lib/badges";
import { formatWhen, initials } from "@/lib/format";
import { cn } from "@/lib/utils";

import {
  getUser,
  listUserKeys,
  mappingMatrix,
  revokeKey,
  terminateSessions,
  usersKeys,
} from "./api";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ROLES, roleLabel } from "./format";
import { IdentityLinkPopover } from "./IdentityLinkPopover";
import { TempPasswordDialog } from "./TempPasswordDialog";
import type { AdminUserDetail } from "./types";
import { useUserActions } from "./useUserActions";

/** Admin · User card: profile, identity links, sessions, API keys and admin
 * actions. Wireframe: admin-panel/_wireframes/user-card.html. */
export function UserCardPage() {
  const { userId = "" } = useParams();
  const id = Number(userId);
  const detail = useQuery({ queryKey: usersKeys.detail(id), queryFn: () => getUser(id) });

  if (detail.isPending) return <PageSkeleton />;
  if (detail.isError) return <PageError onRetry={() => void detail.refetch()} />;
  return <UserCard user={detail.data} />;
}

/** A titled section: icon + label over a hairline, matching the source card. */
function Section({
  icon: Icon,
  title,
  action,
  className,
  contentClassName,
  children,
}: {
  icon: ComponentType<{ className?: string }>;
  title: ReactNode;
  action?: ReactNode;
  className?: string;
  contentClassName?: string;
  children: ReactNode;
}) {
  return (
    <Card className={className}>
      <CardHeader className="items-center border-b">
        <CardTitle className="flex items-center gap-2 text-sm font-semibold">
          <Icon className="text-muted-foreground size-4" aria-hidden="true" />
          {title}
        </CardTitle>
        {action && <CardAction>{action}</CardAction>}
      </CardHeader>
      <CardContent className={contentClassName}>{children}</CardContent>
    </Card>
  );
}

/** One profile fact: muted label, then value/control. */
function Fact({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="flex min-h-8 flex-wrap items-center gap-x-3 gap-y-1">
      <dt className="text-muted-foreground w-24 shrink-0 text-xs">{label}</dt>
      <dd className="flex items-center gap-2 text-sm">{children}</dd>
    </div>
  );
}

type CardDialog = "reset" | "deactivate" | "delete" | "sessions" | null;

function UserCard({ user }: { user: AdminUserDetail }) {
  const { t, i18n } = useTranslation();
  const session = useSession();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const isOwner = isOwnerRole(session.user?.role);
  const isSelf = session.user?.id === user.id;
  // Manage scope mirrors the backend (users_admin.manage_scope_or_403): the owner
  // acts on anyone, an admin only on members. Gating the actions here keeps the
  // card from offering resets/deactivations the server would reject with a 403.
  const canManage = isOwner || isMember(user.role);
  const isActive = user.status === "active";
  const [tempPassword, setTempPassword] = useState<string | null>(null);
  const [emailDraft, setEmailDraft] = useState<string | null>(null);
  const [dialog, setDialog] = useState<CardDialog>(null);

  const closeDialog = () => {
    setDialog(null);
  };
  const { patch, reset, remove } = useUserActions(user, {
    onTempPassword: setTempPassword,
    onDeleted: () => void navigate("/admin/users"),
  });
  const killSessions = useMutation({
    mutationFn: () => terminateSessions(user.id),
    onSuccess: () => {
      toast.success(t("admin.users.card.sessionsTerminated"));
      closeDialog();
      void queryClient.invalidateQueries({ queryKey: usersKeys.detail(user.id) });
    },
    onError: (error) => void toastApiError(error, t("admin.users.card.actionFailed")),
  });

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
      <BackLink to="/admin/users" label={t("admin.nav.users")} />
      <header className="flex items-start gap-4">
        <div
          aria-hidden="true"
          className={cn(
            "grid size-12 shrink-0 place-items-center rounded-2xl text-sm font-semibold tracking-wide",
            isActive ? "bg-muted text-foreground" : "bg-muted text-muted-foreground opacity-70",
          )}
        >
          {initials(user.full_name)}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
            <h1 className="text-2xl font-semibold tracking-tight">{user.full_name}</h1>
            <Badge variant={userStatusBadgeVariant(user.status)}>
              {isActive ? t("admin.users.statuses.active") : t("admin.users.statuses.deactivated")}
            </Badge>
          </div>
          <div className="text-muted-foreground mt-1.5 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-sm">
            <MailIcon className="size-3.5 shrink-0" aria-hidden="true" />
            <span className="truncate">{user.email}</span>
            <Button
              variant="ghost"
              size="xs"
              className="text-muted-foreground hover:text-foreground -ml-0.5 h-6"
              aria-label={t("admin.users.card.changeEmail")}
              onClick={() => {
                setEmailDraft(user.email);
              }}
            >
              <PencilIcon aria-hidden="true" />
            </Button>
          </div>
        </div>
      </header>

      <Section icon={UserRoundIcon} title={t("admin.users.card.profile")}>
        <dl className="flex flex-col gap-2">
          <Fact label={t("admin.users.columns.role")}>
            {isOwner ? (
              <Select
                items={ROLES.map((role) => ({
                  value: role,
                  label: roleLabel(role, t),
                }))}
                value={user.role}
                onValueChange={(role) => {
                  if (role && role !== user.role)
                    patch.mutate(
                      { role },
                      { onSuccess: () => toast.success(t("admin.users.card.roleUpdated")) },
                    );
                }}
              >
                <SelectTrigger size="sm" className="w-32">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {ROLES.map((role) => (
                    <SelectItem key={role} value={role}>
                      {roleLabel(role, t)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : (
              roleLabel(user.role, t)
            )}
          </Fact>
          <Fact label={t("admin.users.card.created")}>
            {formatWhen(user.created_at, i18n.language)}
          </Fact>
          <Fact label={t("admin.users.columns.lastLogin")}>
            {formatWhen(user.last_login_at, i18n.language) ?? t("admin.users.never")}
          </Fact>
        </dl>
      </Section>

      <IdentityCard user={user} />

      <Section
        icon={MonitorSmartphoneIcon}
        title={t("admin.users.card.sessions")}
        contentClassName="flex items-center justify-between gap-4"
      >
        <span className="text-sm">
          {t("admin.users.card.activeSessions", { count: user.active_sessions })}
        </span>
        <Button
          variant="outline"
          size="sm"
          disabled={user.active_sessions === 0 || killSessions.isPending}
          onClick={() => {
            setDialog("sessions");
          }}
        >
          {t("admin.users.card.terminateAll")}
        </Button>
      </Section>

      <KeysCard user={user} />

      {/* Admin actions never target the acting admin (reset routes to the
          profile's own password change, self-deactivate/delete are barred), nor
          a target outside the actor's manage scope — the section is dropped when
          there is nothing the actor may actually do here. */}
      {!isSelf && canManage && (
        <Section icon={SettingsIcon} title={t("admin.users.card.actions")}>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={reset.isPending}
              onClick={() => {
                setDialog("reset");
              }}
            >
              {t("admin.users.card.resetPassword")}
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={patch.isPending}
              onClick={() => {
                if (isActive) setDialog("deactivate");
                else patch.mutate({ status: "active" });
              }}
            >
              {isActive ? t("admin.users.card.deactivate") : t("admin.users.card.reactivate")}
            </Button>
            {isOwner && (
              <Button
                variant="destructive"
                size="sm"
                className="ml-auto"
                onClick={() => {
                  setDialog("delete");
                }}
              >
                {t("admin.users.card.delete")}
              </Button>
            )}
          </div>
        </Section>
      )}

      <TempPasswordDialog
        password={tempPassword}
        onClose={() => {
          setTempPassword(null);
        }}
      />

      <Dialog
        open={emailDraft !== null}
        onOpenChange={(open) => {
          if (!open) setEmailDraft(null);
        }}
      >
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>{t("admin.users.card.changeEmail")}</DialogTitle>
            <DialogDescription>{t("admin.users.card.changeEmailHint")}</DialogDescription>
          </DialogHeader>
          <Input
            type="email"
            value={emailDraft ?? ""}
            onChange={(event) => {
              setEmailDraft(event.target.value);
            }}
          />
          <DialogFooter>
            <Button
              disabled={!emailDraft?.includes("@") || patch.isPending}
              onClick={() => {
                if (!emailDraft) return;
                patch.mutate(
                  { email: emailDraft.trim() },
                  {
                    onSuccess: () => {
                      setEmailDraft(null);
                    },
                  },
                );
              }}
            >
              {t("admin.platform.save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Guard modals (user-card.html): reset — a confirm before the letter;
          deactivate — reversible confirm; delete — type-to-confirm. */}
      <ConfirmDialog
        open={dialog === "reset"}
        onOpenChange={closeDialog}
        title={t("admin.users.card.resetPassword")}
        description={t("admin.users.card.resetConfirmBody", { email: user.email })}
        confirmLabel={t("admin.users.card.resetConfirmAction")}
        pending={reset.isPending}
        onConfirm={() => {
          reset.mutate(undefined, { onSuccess: closeDialog });
        }}
      />
      <ConfirmDialog
        open={dialog === "deactivate"}
        onOpenChange={closeDialog}
        title={t("admin.users.card.deactivateConfirmTitle")}
        description={t("admin.users.card.deactivateConfirmBody", { email: user.email })}
        confirmLabel={t("admin.users.card.deactivate")}
        pending={patch.isPending}
        onConfirm={() => {
          patch.mutate({ status: "deactivated" }, { onSuccess: closeDialog });
        }}
      />
      <ConfirmDialog
        open={dialog === "sessions"}
        onOpenChange={closeDialog}
        title={t("admin.users.card.terminateConfirmTitle")}
        description={t("admin.users.card.terminateConfirmBody", { email: user.email })}
        confirmLabel={t("admin.users.card.terminateAll")}
        destructive
        pending={killSessions.isPending}
        onConfirm={() => {
          killSessions.mutate();
        }}
      />
      <ConfirmDialog
        open={dialog === "delete"}
        onOpenChange={closeDialog}
        title={t("admin.users.card.deleteConfirmTitle")}
        description={t("admin.users.card.deleteConfirmBody", { email: user.email })}
        confirmLabel={t("admin.users.card.delete")}
        destructive
        challenge={user.email}
        challengeLabel={t("admin.users.card.deleteTypeToConfirm")}
        pending={remove.isPending}
        onConfirm={() => {
          remove.mutate(undefined, { onSuccess: closeDialog });
        }}
      />
    </div>
  );
}

/** Identity mapping block (user-card.html, legend 3): one card per source
 * with linked/unmatched state; link and unlink reuse the matrix popover. */
function IdentityCard({ user }: { user: AdminUserDetail }) {
  const { t } = useTranslation();
  // The matrix narrowed by the user's email; the row carries all links.
  const mappingQuery = { q: user.email };
  const mapping = useQuery({
    queryKey: usersKeys.mapping(mappingQuery),
    queryFn: () => mappingMatrix(mappingQuery),
  });

  // The matrix loads on its own, after the page skeleton has already yielded to the
  // card. Standing in for it keeps the sections below from sliding up and back.
  if (mapping.isPending)
    return (
      <Section
        icon={Link2Icon}
        title={t("admin.users.card.identity")}
        contentClassName="divide-border divide-y"
      >
        {[0, 1].map((row) => (
          <div key={row} className="flex min-h-11 items-center gap-2 py-1.5 first:pt-0 last:pb-0">
            <Skeleton className="h-4 w-32" />
            <Skeleton className="h-5 w-44" />
          </div>
        ))}
      </Section>
    );
  if (!mapping.data || mapping.data.sources.length === 0) return null;
  const row = mapping.data.items.find((item) => item.user_id === user.id);

  return (
    <Section
      icon={Link2Icon}
      title={t("admin.users.card.identity")}
      contentClassName="divide-border divide-y"
    >
      {mapping.data.sources.map((source) => {
        const link = row?.links.find((item) => item.source_id === source.id) ?? null;
        return (
          <div
            key={source.id}
            className="flex min-h-11 flex-wrap items-center gap-2 py-1.5 text-sm first:pt-0 last:pb-0"
          >
            <span className="font-medium">{source.name}</span>
            {link ? (
              <>
                <Badge variant="secondary">
                  {link.display_name ?? link.email ?? link.source_user_id}
                </Badge>
                <IdentityLinkPopover
                  userId={user.id}
                  source={source}
                  current={link}
                  trigger={
                    <Button variant="ghost" size="xs" className="ml-auto">
                      {t("admin.users.identity.change")}
                    </Button>
                  }
                />
              </>
            ) : (
              <>
                <Badge variant="warning">{t("admin.users.identity.statuses.unmatched")}</Badge>
                <IdentityLinkPopover
                  userId={user.id}
                  source={source}
                  trigger={
                    <Button variant="ghost" size="xs" className="ml-auto">
                      {t("admin.users.identity.link")}
                    </Button>
                  }
                />
              </>
            )}
          </div>
        );
      })}
    </Section>
  );
}

function KeysCard({ user }: { user: AdminUserDetail }) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [revokeTarget, setRevokeTarget] = useState<ApiKey | null>(null);
  const keys = useQuery({
    queryKey: usersKeys.keys(user.id),
    queryFn: () => listUserKeys(user.id),
  });
  const revoke = useMutation({
    mutationFn: revokeKey,
    onSuccess: () => {
      setRevokeTarget(null);
      void queryClient.invalidateQueries({ queryKey: usersKeys.keys(user.id) });
    },
    onError: (error) => void toastApiError(error, t("admin.users.card.actionFailed")),
  });

  const active = (keys.data?.items ?? []).filter((key) => !key.is_revoked);
  return (
    <Section
      icon={KeyRoundIcon}
      title={t("admin.users.card.apiKeys", { count: active.length })}
      action={
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            setCreateOpen(true);
          }}
        >
          {t("admin.users.card.createKey")}
        </Button>
      }
    >
      {active.length === 0 ? (
        <p className="text-muted-foreground text-sm">{t("admin.users.card.noKeys")}</p>
      ) : (
        <div className="divide-border divide-y">
          {active.map((key) => (
            <div
              key={key.id}
              className="flex min-h-11 flex-wrap items-center gap-2 py-1.5 text-sm first:pt-0 last:pb-0"
            >
              <code className="text-xs">{key.prefix}…</code>
              <Badge variant="outline">{key.scope.access}</Badge>
              <Badge variant="outline">
                {key.scope.sources === null
                  ? t("admin.apiKeys.allSources")
                  : t("admin.apiKeys.sourcesCount", { count: key.scope.sources.length })}
              </Badge>
              <span className="text-muted-foreground text-xs">
                {key.expires_at
                  ? t("admin.users.card.expires", {
                      when: formatWhen(key.expires_at, i18n.language) ?? "",
                    })
                  : t("admin.users.card.noExpiry")}
              </span>
              <Button
                variant="ghost"
                size="xs"
                className="ml-auto"
                disabled={revoke.isPending}
                onClick={() => {
                  setRevokeTarget(key);
                }}
              >
                {t("admin.users.card.revoke")}
              </Button>
            </div>
          ))}
        </div>
      )}

      <CreateKeyDialog open={createOpen} onOpenChange={setCreateOpen} fixedUser={user} />
      <ConfirmDialog
        open={revokeTarget !== null}
        onOpenChange={(open) => {
          if (!open) setRevokeTarget(null);
        }}
        title={t("admin.users.card.revokeKeyConfirmTitle")}
        description={t("admin.users.card.revokeKeyConfirmBody", {
          prefix: revokeTarget?.prefix ?? "",
        })}
        confirmLabel={t("admin.users.card.revoke")}
        destructive
        pending={revoke.isPending}
        onConfirm={() => {
          if (revokeTarget) revoke.mutate(revokeTarget.id);
        }}
      />
    </Section>
  );
}
