import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BracesIcon, Building2Icon, KeyRoundIcon, ServerIcon, WrenchIcon } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "@/lib/toast";

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
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { EmptyState } from "@/components/list-controls/EmptyState";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { TooltipProvider } from "@/components/ui/tooltip";
import { toastApiError } from "@/api/errors";
import { TimezoneCombobox } from "@/components/TimezoneCombobox";
import { isOwner } from "@/features/auth/roles";
import { useSession } from "@/features/auth/session-context";
import { useHashTarget } from "@/lib/useHashTarget";

import { getPlatformSettings, patchPlatformSettings, platformKeys } from "./api";
import { BuildYourOwnCard } from "@/components/BuildYourOwnCard";
import { InfoHint } from "@/components/InfoHint";
import { McpConnect } from "@/components/McpConnect";
import { MattermostCard } from "./MattermostCard";
import { SectionCard } from "./SectionCard";
import { SlackCard } from "./SlackCard";
import { SmtpCard } from "./SmtpCard";
import { TelegramCard } from "./TelegramCard";
import type { PlatformSettings, PlatformSettingsPatch } from "./types";

const MINUTE = 60;
const DAY = 86_400;
/** v1 revocation rests on the access-token window: a deactivated account keeps
 * answering until its token expires, so the backend caps the TTL at an hour. */
const ACCESS_TTL_MAX_MINUTES = 60;

/** Admin · Platform settings: org profile, defaults, session TTLs, integrations,
 * maintenance. Wireframe: admin-panel/_wireframes/platform-settings.html.
 * Owner edits; Admin sees the same screen read-only. */
export function PlatformSettingsPage() {
  const { t } = useTranslation();
  useHashTarget();
  const session = useSession();
  const readOnly = !isOwner(session.user?.role);
  const query = useQuery({ queryKey: platformKeys.settings, queryFn: getPlatformSettings });

  return (
    <TooltipProvider delay={200}>
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
        <header className="flex items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">{t("admin.nav.platform")}</h1>
          {readOnly && <Badge variant="secondary">{t("admin.platform.readOnly")}</Badge>}
        </header>

        {query.isPending ? (
          <div className="flex flex-col gap-5">
            <Skeleton className="h-48 w-full rounded-xl" />
            <Skeleton className="h-32 w-full rounded-xl" />
          </div>
        ) : query.isError ? (
          <EmptyState
            variant="error"
            description={t("common.list.errorTitle")}
            onRetry={() => {
              void query.refetch();
            }}
          />
        ) : (
          <div className="flex flex-col gap-5">
            <OrgCard settings={query.data} readOnly={readOnly} />
            <SessionCard settings={query.data} readOnly={readOnly} />
            <SmtpCard readOnly={readOnly} />

            <GroupLabel
              title={t("admin.platform.surfaces.title")}
              subtitle={t("admin.platform.surfaces.subtitle")}
            />
            <SlackCard readOnly={readOnly} />
            <MattermostCard readOnly={readOnly} />
            <TelegramCard readOnly={readOnly} />
            <ConfirmToggleCard
              icon={ServerIcon}
              title={t("admin.platform.mcp.title")}
              subtitle={t("admin.platform.mcp.subtitle")}
              checked={query.data.mcp_enabled}
              readOnly={readOnly}
              field="mcp_enabled"
              confirmOn="disable"
              confirmTitle={t("admin.platform.mcp.confirmTitle")}
              confirmBody={t("admin.platform.mcp.confirmBody")}
            >
              <McpConnect />
            </ConfirmToggleCard>
            <BuildYourOwnCard
              icon={BracesIcon}
              title={t("admin.platform.publicApi.title")}
              description={t("admin.platform.publicApi.desc")}
              doc="public-api/index.html#contract-card"
            />

            <ConfirmToggleCard
              icon={WrenchIcon}
              title={t("admin.platform.maintenance.title")}
              subtitle={t("admin.platform.maintenance.subtitle")}
              checked={query.data.maintenance_mode}
              readOnly={readOnly}
              field="maintenance_mode"
              confirmOn="enable"
              confirmTitle={t("admin.platform.maintenance.confirmTitle")}
              confirmBody={t("admin.platform.maintenance.confirmBody")}
            />
          </div>
        )}
      </div>
    </TooltipProvider>
  );
}

function useSaveSettings() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: patchPlatformSettings,
    onSuccess: (fresh) => {
      queryClient.setQueryData(platformKeys.settings, fresh);
      void queryClient.invalidateQueries({ queryKey: platformKeys.branding });
      toast.success(t("admin.platform.saved"));
    },
    onError: (error) => void toastApiError(error, t("admin.platform.saveFailed")),
  });
}

function Field({
  id,
  label,
  children,
  hint,
}: {
  id?: string;
  label: string;
  children: React.ReactNode;
  hint?: string;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id} className="flex items-center gap-1.5">
        {label}
        {hint && <InfoHint text={hint} label={label} />}
      </Label>
      {children}
    </div>
  );
}

function OrgCard({ settings, readOnly }: { settings: PlatformSettings; readOnly: boolean }) {
  const { t } = useTranslation();
  const save = useSaveSettings();
  const [draft, setDraft] = useState<PlatformSettingsPatch | null>(null);

  const value = { ...settings, ...draft };
  const set = (patch: PlatformSettingsPatch) => {
    setDraft({ ...draft, ...patch });
  };

  return (
    <SectionCard
      icon={Building2Icon}
      title={t("admin.platform.org.title")}
      subtitle={t("admin.platform.org.subtitle")}
    >
      <div className="flex flex-col gap-4">
        <div className="flex flex-wrap gap-6">
          <Field id="org-name" label={t("admin.platform.org.name")}>
            <Input
              id="org-name"
              className="w-64"
              value={value.org_name}
              disabled={readOnly}
              onChange={(e) => {
                set({ org_name: e.target.value });
              }}
            />
          </Field>
          <Field
            id="org-accent"
            label={t("admin.platform.org.accent")}
            hint={t("admin.platform.org.brandingHint")}
          >
            <div className="flex items-center gap-2">
              <span
                aria-hidden
                className="border-border inline-block size-6 rounded-full border"
                style={{ backgroundColor: value.accent_color }}
              />
              <Input
                id="org-accent"
                className="w-28"
                value={value.accent_color}
                disabled={readOnly}
                onChange={(e) => {
                  set({ accent_color: e.target.value });
                }}
              />
            </div>
          </Field>
          <Field id="org-logo" label={t("admin.platform.org.logoUrl")}>
            <Input
              id="org-logo"
              className="w-72"
              value={value.org_logo_url ?? ""}
              placeholder="https://…"
              disabled={readOnly}
              onChange={(e) => {
                set({ org_logo_url: e.target.value || null });
              }}
            />
          </Field>
        </div>
        <Field id="org-description" label={t("admin.platform.org.description")}>
          <Textarea
            id="org-description"
            rows={2}
            value={value.org_description ?? ""}
            disabled={readOnly}
            onChange={(e) => {
              set({ org_description: e.target.value || null });
            }}
          />
        </Field>

        <div className="flex flex-wrap items-end gap-6">
          <Field
            id="org-timezone"
            label={t("admin.platform.org.timezone")}
            hint={t("admin.platform.org.defaultsHint")}
          >
            <TimezoneCombobox
              id="org-timezone"
              value={value.timezone || null}
              disabled={readOnly}
              onValueChange={(timezone) => {
                if (timezone) set({ timezone });
              }}
              placeholder={t("admin.platform.org.timezonePlaceholder")}
              emptyLabel={t("admin.platform.org.timezoneEmpty")}
              detectedLabel={t("admin.platform.org.timezoneDetected")}
            />
          </Field>
          <Field label={t("admin.platform.org.dateFormat")}>
            <Select
              value={value.date_format}
              onValueChange={(date_format) => {
                if (date_format) set({ date_format });
              }}
            >
              <SelectTrigger className="w-40" disabled={readOnly}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {settings.date_format_choices.map((format) => (
                  <SelectItem key={format} value={format}>
                    {format}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <Field label={t("admin.platform.org.locale")}>
            <Select
              items={settings.locale_choices.map((locale) => ({
                value: locale,
                label: locale.toUpperCase(),
              }))}
              value={value.locale}
              onValueChange={(locale) => {
                if (locale) set({ locale });
              }}
            >
              <SelectTrigger className="w-28" disabled={readOnly}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {settings.locale_choices.map((locale) => (
                  <SelectItem key={locale} value={locale}>
                    {locale.toUpperCase()}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
        </div>

        {!readOnly && (
          <Button
            size="sm"
            className="self-end"
            disabled={draft === null || save.isPending}
            onClick={() => {
              if (!draft) return;
              save.mutate(draft, {
                onSuccess: () => {
                  setDraft(null);
                },
              });
            }}
          >
            {t("admin.platform.save")}
          </Button>
        )}
      </div>
    </SectionCard>
  );
}

function SessionCard({ settings, readOnly }: { settings: PlatformSettings; readOnly: boolean }) {
  const { t } = useTranslation();
  const save = useSaveSettings();
  const [draft, setDraft] = useState<{
    accessMinutes: string;
    refreshDays: string;
    absoluteDays: string;
  } | null>(null);

  const values = draft ?? {
    accessMinutes: String(settings.access_token_ttl / MINUTE),
    refreshDays: String(settings.refresh_token_ttl / DAY),
    absoluteDays: String(settings.session_absolute_ttl / DAY),
  };
  const positive =
    Number(values.accessMinutes) > 0 &&
    Number(values.accessMinutes) <= ACCESS_TTL_MAX_MINUTES &&
    Number(values.refreshDays) > 0 &&
    Number(values.absoluteDays) > 0;
  // Access is capped at an hour, so only the outer two can invert each other.
  const nested = Number(values.refreshDays) <= Number(values.absoluteDays);

  const ttlField = (
    id: string,
    label: string,
    key: keyof typeof values,
    unit: string,
    max?: number,
    hint?: string,
  ): React.ReactNode => (
    <Field id={id} label={label} hint={hint}>
      <div className="flex items-center gap-2">
        <Input
          id={id}
          type="number"
          min={1}
          max={max}
          className="w-24"
          value={values[key]}
          disabled={readOnly}
          onChange={(e) => {
            setDraft({ ...values, [key]: e.target.value });
          }}
        />
        <span className="text-muted-foreground text-xs">{unit}</span>
      </div>
    </Field>
  );

  return (
    <SectionCard
      icon={KeyRoundIcon}
      title={t("admin.platform.session.title")}
      subtitle={t("admin.platform.session.subtitle")}
    >
      <div className="flex flex-col gap-4">
        <div className="flex flex-wrap items-end gap-6">
          {ttlField(
            "ttl-access",
            t("admin.platform.session.accessTtl"),
            "accessMinutes",
            t("admin.platform.session.minutes"),
            ACCESS_TTL_MAX_MINUTES,
            t("admin.platform.session.accessTtlHint"),
          )}
          {ttlField(
            "ttl-refresh",
            t("admin.platform.session.refreshTtl"),
            "refreshDays",
            t("admin.platform.session.days"),
          )}
          {ttlField(
            "ttl-absolute",
            t("admin.platform.session.absoluteTtl"),
            "absoluteDays",
            t("admin.platform.session.days"),
          )}
        </div>
        {!nested && (
          <p className="text-destructive text-xs">{t("admin.platform.session.nesting")}</p>
        )}
        {!readOnly && (
          <Button
            size="sm"
            className="self-end"
            disabled={draft === null || !positive || !nested || save.isPending}
            onClick={() => {
              save.mutate(
                {
                  access_token_ttl: Math.round(Number(values.accessMinutes) * MINUTE),
                  refresh_token_ttl: Math.round(Number(values.refreshDays) * DAY),
                  session_absolute_ttl: Math.round(Number(values.absoluteDays) * DAY),
                },
                {
                  onSuccess: () => {
                    setDraft(null);
                  },
                },
              );
            }}
          >
            {t("admin.platform.save")}
          </Button>
        )}
      </div>
    </SectionCard>
  );
}

/** A quiet eyebrow that groups the cards below it into one block (e.g. the
 * employee-facing surfaces) without wrapping them in another frame. */
function GroupLabel({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div className="px-1 pt-2">
      <p className="text-muted-foreground text-xs font-semibold tracking-wider uppercase">
        {title}
      </p>
      {subtitle && <p className="text-muted-foreground/70 mt-0.5 text-xs">{subtitle}</p>}
    </div>
  );
}

/** A card whose whole point is one master switch, where flipping it the
 * disruptive way (turning MCP off, maintenance on) asks for confirmation first.
 * Header-only by default; pass `children` for a body (the MCP block tucks its
 * "how to connect" panel there). Backs both the MCP-server and maintenance blocks. */
function ConfirmToggleCard({
  icon,
  title,
  subtitle,
  checked,
  readOnly,
  field,
  confirmOn,
  confirmTitle,
  confirmBody,
  children,
}: {
  icon: LucideIcon;
  title: string;
  subtitle: string;
  checked: boolean;
  readOnly: boolean;
  field: "mcp_enabled" | "maintenance_mode";
  /** Which direction is disruptive and needs a confirmation. */
  confirmOn: "enable" | "disable";
  confirmTitle: string;
  confirmBody: string;
  /** Optional card body shown under the header (e.g. the MCP connect panel). */
  children?: React.ReactNode;
}) {
  const { t } = useTranslation();
  const save = useSaveSettings();
  const [confirming, setConfirming] = useState(false);

  const apply = (next: boolean) => {
    save.mutate({ [field]: next });
  };

  return (
    <>
      <SectionCard
        icon={icon}
        title={title}
        subtitle={subtitle}
        aside={
          <Switch
            checked={checked}
            disabled={readOnly || save.isPending}
            onCheckedChange={(next) => {
              const risky = confirmOn === "enable" ? next : !next;
              if (risky) setConfirming(true);
              else apply(next);
            }}
          />
        }
      >
        {children}
      </SectionCard>
      <AlertDialog
        open={confirming}
        onOpenChange={(open) => {
          if (!open) setConfirming(false);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{confirmTitle}</AlertDialogTitle>
            <AlertDialogDescription>{confirmBody}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("admin.platform.cancel")}</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                apply(confirmOn === "enable");
                setConfirming(false);
              }}
            >
              {t("admin.platform.confirm")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
