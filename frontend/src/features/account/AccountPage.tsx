import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronDownIcon,
  ChevronRightIcon,
  HashIcon,
  KeyRoundIcon,
  MessagesSquareIcon,
  PencilLineIcon,
  SendIcon,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { toast } from "@/lib/toast";

import { apiErrorReason, toastApiError } from "@/api/errors";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { InfoHint } from "@/components/InfoHint";
import { McpConnect } from "@/components/McpConnect";
import { TimezoneCombobox } from "@/components/TimezoneCombobox";
import { TruncatedText } from "@/components/TruncatedText";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { changePassword, logoutAll } from "@/features/auth/api";
import { PasswordField } from "@/features/auth/AuthCard";
import { roleLabel } from "@/features/auth/roles";
import { useSession } from "@/features/auth/session-context";
import { updateSessionUser } from "@/features/auth/session-store";
import { API_KEY_NAME_MAX_LEN } from "@/features/auth/api-keys";
import { clearStoredLocale, setLocale, type AppLocale } from "@/i18n";
import { formatWhen, initials } from "@/lib/format";
import { cn } from "@/lib/utils";

import { accountKeys, getMe, listMyKeys, renameMyKey, revokeMyKey, updateProfile } from "./api";
import { CreateMyKeyDialog } from "./CreateMyKeyDialog";
import { LINK_PLATFORMS, PLATFORM_NAMES, type LinkPlatform } from "./link-platforms";

/** Personal account: profile facts, region, password, own API keys, sessions,
 * messenger link. Wireframe: auth-security/_wireframes/profile-account.html. */
export function AccountPage() {
  const { t } = useTranslation();
  const session = useSession();
  const [signOutAllOpen, setSignOutAllOpen] = useState(false);
  const user = session.status === "authenticated" ? session.user : null;
  if (!user) return null;

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex w-full max-w-2xl flex-col gap-4 px-6 py-8">
        <div className="flex items-center gap-4">
          <span
            aria-hidden
            className="bg-secondary text-secondary-foreground ring-border flex size-14 shrink-0 items-center justify-center rounded-full text-lg font-semibold ring-1"
          >
            {initials(user.full_name)}
          </span>
          <div className="flex min-w-0 flex-col gap-0.5">
            <div className="flex items-center gap-2">
              <h1 className="min-w-0 text-2xl font-semibold tracking-tight">
                <TruncatedText>{user.full_name || user.email}</TruncatedText>
              </h1>
              <Badge variant="secondary" className="shrink-0">
                {roleLabel(user.role, t)}
              </Badge>
            </div>
            <TruncatedText className="text-muted-foreground text-sm">{user.email}</TruncatedText>
          </div>
        </div>

        <ProfileCard />
        <RegionCard />
        <NotificationsCard />
        <PasswordCard />
        <KeysCard />
        <ConnectedCard />

        <Card>
          <CardHeader className="flex flex-row items-center justify-between gap-3">
            <CardTitle className="text-sm font-semibold">{t("account.sessions.title")}</CardTitle>
            <div className="flex shrink-0 gap-2">
              <Button variant="outline" size="sm" render={<Link to="/account/sessions" />}>
                {t("account.sessions.manage")}
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setSignOutAllOpen(true);
                }}
              >
                {t("common.header.signOutAll")}
              </Button>
            </div>
          </CardHeader>
        </Card>
      </div>

      <ConfirmDialog
        open={signOutAllOpen}
        onOpenChange={setSignOutAllOpen}
        title={t("common.header.signOutAllTitle")}
        description={t("common.header.signOutAllBody")}
        confirmLabel={t("common.header.signOutAll")}
        destructive
        onConfirm={() => {
          void logoutAll();
        }}
      />
    </div>
  );
}

function ProfileCard() {
  const { t } = useTranslation();
  const session = useSession();
  const user = session.status === "authenticated" ? session.user : null;
  const [name, setName] = useState(user?.full_name ?? "");

  const save = useMutation({
    mutationFn: (fullName: string) => updateProfile({ full_name: fullName }),
    onSuccess: (updated) => {
      updateSessionUser(updated);
      toast.success(t("account.profile.saved"));
    },
    onError: (err) => void toastApiError(err, t("account.profile.failed")),
  });

  if (!user) return null;
  const dirty = name.trim().length > 0 && name !== user.full_name;

  return (
    <Card>
      <CardHeader className="border-b">
        <CardTitle className="text-sm font-semibold">{t("account.profile.title")}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="flex w-72 max-w-full flex-col gap-1.5">
            <Label htmlFor="account-name">{t("account.profile.name")}</Label>
            <Input
              id="account-name"
              value={name}
              onChange={(event) => {
                setName(event.target.value);
              }}
            />
          </div>
          <Button
            size="sm"
            disabled={!dirty || save.isPending}
            onClick={() => {
              save.mutate(name.trim());
            }}
          >
            {t("account.profile.save")}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

/** Select has no empty-string option (that slot belongs to the placeholder), so
 * the "inherit the org default" choice travels under a sentinel and is mapped
 * back to `null` — the value the backend reads as "clear my override". */
const ORG_DEFAULT = "__org_default__";

function RegionCard() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const me = useQuery({ queryKey: accountKeys.me, queryFn: getMe });
  // null = untouched (omit from the patch); "" = cleared to the org default;
  // a value = set it. Each field can be cleared: the selects via their
  // org-default item, the timezone via the combobox clear button.
  const [timezone, setTimezone] = useState<string | null>(null);
  const [locale, setLocaleValue] = useState<string | null>(null);
  const [dateFormat, setDateFormat] = useState<string | null>(null);

  /** "" (cleared) → null on the wire; untouched → absent from the patch. */
  const patchValue = (draft: string | null) => (draft === null ? undefined : draft.trim() || null);

  const save = useMutation({
    mutationFn: () =>
      updateProfile({
        timezone: patchValue(timezone),
        locale: patchValue(locale),
        date_format: patchValue(dateFormat),
      }),
    onSuccess: (updated) => {
      // Only a save that touched the language may move the device override: pin it
      // to this device while a personal locale exists, drop it when cleared so the
      // org default can surface. A save of the neighbouring fields leaves it alone,
      // or a language picked from the header would vanish on an unrelated edit.
      if (locale !== null) {
        if (updated.locale) setLocale(updated.locale as AppLocale, updated.id);
        else clearStoredLocale();
      }
      updateSessionUser(updated);
      // Drop the local overlays and refetch, so the form re-syncs from the server.
      setTimezone(null);
      setLocaleValue(null);
      setDateFormat(null);
      void queryClient.invalidateQueries({ queryKey: accountKeys.me });
      toast.success(t("account.region.saved"));
    },
    onError: (err) => void toastApiError(err, t("account.region.failed")),
  });

  // The card keeps its footprint while /me is in flight; returning null here would
  // let every card below it jump up and back down once the fields land.
  if (!me.data) return <RegionCardSkeleton />;
  const current = me.data.user;
  const tz = timezone ?? current.timezone ?? "";
  const loc = locale ?? current.locale ?? "";
  const df = dateFormat ?? current.date_format ?? "";
  // An untouched field is not dirty; a cleared one ("") differs from a set value,
  // and matches a server-side null once both are normalized to "".
  const changed = (draft: string | null, saved: string | null) =>
    draft !== null && draft !== (saved ?? "");
  const dirty =
    changed(timezone, current.timezone) ||
    changed(locale, current.locale) ||
    changed(dateFormat, current.date_format);

  return (
    <Card>
      <CardHeader className="border-b">
        <CardTitle className="text-sm font-semibold">{t("account.region.title")}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex flex-wrap items-start gap-6">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="account-timezone">{t("account.region.timezone")}</Label>
            <TimezoneCombobox
              id="account-timezone"
              value={tz || null}
              onValueChange={(value) => {
                setTimezone(value ?? "");
              }}
              placeholder={t("account.region.timezonePlaceholder")}
              emptyLabel={t("account.region.timezoneEmpty")}
              detectedLabel={t("account.region.timezoneDetected")}
              clearLabel={t("account.region.clear")}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="account-locale">{t("account.region.language")}</Label>
            <Select
              value={loc || ORG_DEFAULT}
              onValueChange={(value) => {
                if (value) setLocaleValue(value === ORG_DEFAULT ? "" : value);
              }}
            >
              <SelectTrigger id="account-locale" className="w-44">
                {/* Without a render function the trigger would echo the raw
                    value — the sentinel included. */}
                <SelectValue>
                  {(value: string) =>
                    value === ORG_DEFAULT ? t("account.region.orgDefault") : value.toUpperCase()
                  }
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ORG_DEFAULT}>{t("account.region.orgDefault")}</SelectItem>
                {me.data.locale_choices.map((choice) => (
                  <SelectItem key={choice} value={choice}>
                    {choice.toUpperCase()}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {/* Full width, so this row always wraps below the timezone and language
              pair — and the save button can sit at the card's right edge. */}
          <div className="flex w-full flex-wrap items-end justify-between gap-3">
            <div className="flex w-64 max-w-full flex-col gap-1.5">
              <Label htmlFor="account-date-format">{t("account.region.dateFormat")}</Label>
              <Select
                value={df || ORG_DEFAULT}
                onValueChange={(value) => {
                  if (value) setDateFormat(value === ORG_DEFAULT ? "" : value);
                }}
              >
                <SelectTrigger id="account-date-format" className="w-full">
                  <SelectValue>
                    {(value: string) =>
                      value === ORG_DEFAULT ? t("account.region.orgDefault") : value
                    }
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ORG_DEFAULT}>{t("account.region.orgDefault")}</SelectItem>
                  {me.data.date_format_choices.map((choice) => (
                    <SelectItem key={choice} value={choice}>
                      {choice}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <Button
              size="sm"
              disabled={!dirty || save.isPending}
              onClick={() => {
                save.mutate();
              }}
            >
              {t("account.region.save")}
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

/** Mirrors RegionCard's grid so the placeholder occupies the same height. */
function RegionCardSkeleton() {
  const { t } = useTranslation();
  const field = (width: string) => (
    <div className="flex flex-col gap-1.5">
      <Skeleton className="h-4 w-24" />
      <Skeleton className={cn("h-9", width)} />
    </div>
  );

  return (
    <Card>
      <CardHeader className="border-b">
        <CardTitle className="text-sm font-semibold">{t("account.region.title")}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex flex-wrap items-start gap-6">
          {field("w-64")}
          {field("w-44")}
          <div className="flex w-full flex-wrap items-end justify-between gap-3">
            {field("w-64")}
            <Skeleton className="h-8 w-20" />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function NotificationsCard() {
  const { t } = useTranslation();
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-3">
        <CardTitle className="text-sm font-semibold">{t("account.notifications.title")}</CardTitle>
        <Button
          variant="outline"
          size="sm"
          render={
            <Link
              to="/inbox/settings"
              state={{ back: { to: "/account", label: t("account.title") } }}
            />
          }
        >
          {t("account.notifications.manage")}
        </Button>
      </CardHeader>
    </Card>
  );
}

/** Same glyphs the admin Platform screen gives each messenger, so a surface reads
 * the same in both places (SlackCard/TelegramCard/MattermostCard). */
const PLATFORM_ICONS: Record<LinkPlatform, LucideIcon> = {
  slack: HashIcon,
  telegram: SendIcon,
  mattermost: MessagesSquareIcon,
};

function ConnectedCard() {
  const { t } = useTranslation();
  // Static keys — a computed `account.connected.platforms.${platform}` would trip
  // the typed-key inference (TS2589), so the taglines are looked up by literal key.
  const tagline: Record<LinkPlatform, string> = {
    slack: t("account.connected.platforms.slack"),
    telegram: t("account.connected.platforms.telegram"),
    mattermost: t("account.connected.platforms.mattermost"),
  };

  return (
    <Card>
      <CardHeader className="border-b">
        <CardTitle className="flex items-center gap-1.5 text-sm font-semibold">
          {t("account.connected.title")}
          <InfoHint text={t("account.connected.hint")} />
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="divide-border divide-y overflow-hidden rounded-lg border">
          {LINK_PLATFORMS.map((platform) => {
            const Icon = PLATFORM_ICONS[platform];
            return (
              <div key={platform} className="flex items-center gap-3 px-3 py-3">
                <span className="bg-muted/70 text-muted-foreground grid size-9 shrink-0 place-items-center rounded-lg">
                  <Icon className="size-[1.15rem]" strokeWidth={1.75} />
                </span>
                <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <span className="text-sm font-medium">{PLATFORM_NAMES[platform]}</span>
                  <TruncatedText className="text-muted-foreground text-xs">
                    {tagline[platform]}
                  </TruncatedText>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  className="shrink-0"
                  aria-label={t("account.connected.link", { platform: PLATFORM_NAMES[platform] })}
                  render={<Link to={`/link/${platform}`} />}
                >
                  {t("account.connected.linkAction")}
                </Button>
              </div>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}

function PasswordCard() {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);

  const reset = () => {
    setCurrent("");
    setNext("");
    setConfirm("");
    setError(null);
  };

  const change = useMutation({
    mutationFn: () => changePassword(current, next),
    onSuccess: () => {
      toast.success(t("account.password.changed"));
      reset();
      setOpen(false);
    },
    onError: async (err) => {
      setError(await apiErrorReason(err));
    },
  });

  const mismatch = confirm.length > 0 && next !== confirm;
  const ready = current.length > 0 && next.length > 0 && next === confirm;

  const field = (
    id: string,
    label: string,
    value: string,
    onChange: (value: string) => void,
    opts?: { autoComplete?: string; strength?: boolean },
  ) => (
    <div className="max-w-72">
      <PasswordField
        id={id}
        label={label}
        value={value}
        onChange={onChange}
        autoComplete={opts?.autoComplete ?? "new-password"}
        showStrength={opts?.strength}
      />
    </div>
  );

  return (
    <Card>
      <CardHeader
        className={cn("flex flex-row items-center justify-between gap-3", open && "border-b")}
      >
        <CardTitle className="text-sm font-semibold">{t("account.password.title")}</CardTitle>
        <Button
          variant="outline"
          size="sm"
          className="shrink-0"
          onClick={() => {
            if (open) reset();
            setOpen((value) => !value);
          }}
        >
          {open ? t("common.cancel") : t("account.password.change")}
        </Button>
      </CardHeader>
      {open && (
        <CardContent className="flex flex-col gap-4">
          {field("account-password-current", t("account.password.current"), current, setCurrent, {
            autoComplete: "current-password",
          })}
          {field("account-password-new", t("account.password.new"), next, setNext, {
            strength: true,
          })}
          {field("account-password-confirm", t("account.password.confirm"), confirm, setConfirm)}
          {mismatch && <p className="text-destructive text-sm">{t("account.password.mismatch")}</p>}
          {error && <p className="text-destructive text-sm">{error}</p>}
          <Button
            size="sm"
            className="self-end"
            disabled={!ready || change.isPending}
            onClick={() => {
              change.mutate();
            }}
          >
            {t("account.password.submit")}
          </Button>
        </CardContent>
      )}
    </Card>
  );
}

/** How many active keys to show before collapsing the rest behind a toggle. */
const KEY_PREVIEW_LIMIT = 4;

function KeysCard() {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [showRevoked, setShowRevoked] = useState(false);
  const [showAllKeys, setShowAllKeys] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const keys = useQuery({ queryKey: accountKeys.apiKeys, queryFn: listMyKeys });

  const revoke = useMutation({
    mutationFn: revokeMyKey,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: accountKeys.apiKeys }),
  });

  const rename = useMutation({
    mutationFn: ({ id, name }: { id: number; name: string | null }) => renameMyKey(id, name),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: accountKeys.apiKeys }),
  });
  const startRename = (id: number, current: string | null) => {
    setEditingId(id);
    setDraft(current ?? "");
  };
  // Blank clears the label back to the prefix display; Escape drops the edit.
  const commitRename = () => {
    if (editingId !== null) rename.mutate({ id: editingId, name: draft.trim() || null });
    setEditingId(null);
  };

  const all = keys.data?.items ?? [];
  const active = all.filter((key) => !key.is_revoked);
  const revoked = all.filter((key) => key.is_revoked);
  const collapsible = active.length > KEY_PREVIEW_LIMIT;
  const visible = collapsible && !showAllKeys ? active.slice(0, KEY_PREVIEW_LIMIT) : active;
  // The signature under a key: its life span (or the moment it ended) and last call.
  const meta = (key: (typeof all)[number]) => {
    const when = (at: string) => formatWhen(at, i18n.language) ?? "";
    const lifespan = key.is_revoked
      ? [
          key.revoked_at
            ? t("account.keys.revokedOn", { when: when(key.revoked_at) })
            : t("account.keys.revokedLabel"),
        ]
      : [
          t("account.keys.created", { when: when(key.created_at) }),
          key.expires_at
            ? t("account.keys.expires", { when: when(key.expires_at) })
            : t("account.keys.noExpiry"),
        ];
    return [
      ...lifespan,
      key.last_used_at
        ? t("account.keys.lastUsed", { when: when(key.last_used_at) })
        : t("account.keys.neverUsed"),
    ].join(" · ");
  };

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-3 border-b">
        <CardTitle className="flex items-center gap-1.5 text-sm font-semibold">
          {t("account.keys.title")}
          <InfoHint text={t("account.keys.subtitle")} />
        </CardTitle>
        <Button
          size="sm"
          className="shrink-0"
          onClick={() => {
            setCreating(true);
          }}
        >
          {t("account.keys.create")}
        </Button>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <CreateMyKeyDialog open={creating} onOpenChange={setCreating} />
        {keys.isPending ? (
          // A key-row-shaped placeholder: the empty state is twice as tall, and
          // showing it before the list arrives makes the card collapse on load.
          <div className="divide-border divide-y overflow-hidden rounded-lg border">
            <div className="flex items-center gap-3 px-3 py-2.5">
              <Skeleton className="size-4 shrink-0 rounded-sm" />
              <div className="flex flex-col gap-1.5">
                <Skeleton className="h-4 w-24" />
                <Skeleton className="h-3 w-56" />
              </div>
              <Skeleton className="ml-auto h-7 w-16" />
            </div>
          </div>
        ) : active.length === 0 ? (
          <div className="border-border/60 flex flex-col items-center gap-2 rounded-lg border border-dashed py-8 text-center">
            <KeyRoundIcon className="text-muted-foreground/70 size-6" />
            <div className="flex flex-col gap-0.5">
              <span className="text-sm font-medium">{t("account.keys.emptyTitle")}</span>
              <span className="text-muted-foreground text-xs">{t("account.keys.emptyHint")}</span>
            </div>
          </div>
        ) : (
          <div className="divide-border divide-y overflow-hidden rounded-lg border">
            {visible.map((key) => (
              <div key={key.id} className="flex items-center gap-3 px-3 py-2.5">
                <KeyRoundIcon className="text-muted-foreground size-4 shrink-0" />
                <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                  {editingId === key.id ? (
                    <Input
                      // Focus on mount without the flagged autoFocus prop.
                      ref={(el) => {
                        el?.focus();
                      }}
                      value={draft}
                      maxLength={API_KEY_NAME_MAX_LEN}
                      placeholder={t("account.keys.namePlaceholder")}
                      className="h-7"
                      onChange={(event) => {
                        setDraft(event.target.value);
                      }}
                      onBlur={commitRename}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") commitRename();
                        else if (event.key === "Escape") setEditingId(null);
                      }}
                    />
                  ) : (
                    <div className="flex min-w-0 items-center gap-1.5">
                      {key.name ? (
                        <TruncatedText className="text-sm">{key.name}</TruncatedText>
                      ) : (
                        <code className="text-sm">{key.prefix}…</code>
                      )}
                      <button
                        type="button"
                        className="text-muted-foreground hover:text-foreground shrink-0 transition-colors"
                        title={t("account.keys.rename")}
                        aria-label={t("account.keys.rename")}
                        onClick={() => {
                          startRename(key.id, key.name);
                        }}
                      >
                        <PencilLineIcon className="size-3.5" />
                      </button>
                    </div>
                  )}
                  <span className="text-muted-foreground text-xs">
                    {key.name ? `${key.prefix}… · ${meta(key)}` : meta(key)}
                  </span>
                </div>
                <Button
                  variant="outline"
                  size="xs"
                  className="shrink-0"
                  disabled={revoke.isPending}
                  onClick={() => {
                    revoke.mutate(key.id);
                  }}
                >
                  {t("account.keys.revoke")}
                </Button>
              </div>
            ))}
            {collapsible && (
              <button
                type="button"
                className="text-muted-foreground hover:text-foreground hover:bg-muted/40 flex w-full items-center justify-center gap-1.5 px-3 py-2 text-xs font-medium transition-colors"
                aria-expanded={showAllKeys}
                onClick={() => {
                  setShowAllKeys((open) => !open);
                }}
              >
                <ChevronDownIcon
                  className={cn("size-3.5 transition-transform", showAllKeys && "rotate-180")}
                />
                {showAllKeys
                  ? t("account.keys.showLess")
                  : t("account.keys.showAll", { n: active.length })}
              </button>
            )}
          </div>
        )}
        {revoked.length > 0 && (
          <div className="flex flex-col gap-2">
            <button
              type="button"
              className="text-muted-foreground hover:text-foreground flex items-center gap-1.5 self-start text-xs transition-colors"
              aria-expanded={showRevoked}
              onClick={() => {
                setShowRevoked((open) => !open);
              }}
            >
              <ChevronRightIcon
                className={cn("size-3.5 transition-transform", showRevoked && "rotate-90")}
              />
              {t("account.keys.revokedTitle", { n: revoked.length })}
            </button>
            {showRevoked && (
              <div className="divide-border/60 border-border/60 divide-y overflow-hidden rounded-lg border border-dashed">
                {revoked.map((key) => (
                  <div key={key.id} className="flex items-center gap-3 px-3 py-2.5 opacity-60">
                    <KeyRoundIcon className="text-muted-foreground size-4 shrink-0" />
                    <div className="flex min-w-0 flex-col gap-0.5">
                      {key.name ? (
                        <TruncatedText className="text-sm line-through">{key.name}</TruncatedText>
                      ) : (
                        <code className="text-sm line-through">{key.prefix}…</code>
                      )}
                      <span className="text-muted-foreground text-xs">
                        {key.name ? `${key.prefix}… · ${meta(key)}` : meta(key)}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
        <div className="border-border/60 border-t pt-4">
          <McpConnect />
        </div>
      </CardContent>
    </Card>
  );
}
