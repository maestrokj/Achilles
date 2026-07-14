import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckIcon, CopyIcon, DatabaseIcon, LockIcon, SearchIcon, XIcon } from "lucide-react";
import { type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams } from "react-router-dom";
import { toastApiError } from "@/api/errors";
import { LIVE_STALE_TIME } from "@/api/freshness";
import { toast } from "@/lib/toast";

import { BackLink } from "@/components/BackLink";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { InfoHint } from "@/components/InfoHint";
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
import { Card, CardAction, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { runStateBadgeVariant, runStateLabel } from "@/lib/badges";
import { WEEKDAYS, formatDurationBetween, formatWhen, weekdayLong } from "@/lib/format";

import {
  cancelSync,
  deleteSource,
  getCatalog,
  getSource,
  harvesterKeys,
  listConnectorTypes,
  listRuns,
  rotateWebhookSecret,
  testConnection,
  usePatchSource,
} from "./api";
import { healthBadgeVariant, stateBadgeVariant } from "./badges";
import { SyncDialog } from "./SyncDialog";
import { MINUTES_PER_DAY, minuteToTime } from "./timeOfDay";
import { TimeOfDayInput } from "./TimeOfDayInput";
import type { CatalogItem, Diagnosis, Source } from "./types";

const AUTHORITY_TIERS = ["high", "normal", "low"] as const;
/** Starting override when "custom schedule" is switched on: weekly sweep,
 * Sunday 03:00 — mirrors the platform seed of reconcile_minute_of_week. */
const DEFAULT_RECONCILE_INTERVAL_DAYS = 7;
const DEFAULT_RECONCILE_WINDOW = 6 * MINUTES_PER_DAY + 180;
/** SyncRun.trigger values with a human label (harvester/constants.py). */
const RUN_TRIGGERS = ["connect", "schedule", "webhook", "watchdog", "manual"] as const;

/** Vertical labelled field: label on top, control, optional hint below. */
function Field({
  label,
  htmlFor,
  hint,
  children,
}: {
  label: ReactNode;
  htmlFor?: string;
  hint?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
      {hint && <p className="text-muted-foreground max-w-96 text-xs">{hint}</p>}
    </div>
  );
}

/** Horizontal setting: label + optional hint on the left, control on the right. */
function SettingRow({
  label,
  hint,
  control,
}: {
  label: ReactNode;
  hint?: ReactNode;
  control: ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-6">
      <div className="min-w-0">
        <p className="text-sm font-medium">{label}</p>
        {hint && <p className="text-muted-foreground max-w-96 text-xs">{hint}</p>}
      </div>
      <div className="shrink-0">{control}</div>
    </div>
  );
}

/** Admin · source card: config, probe, scope, schedule overrides, history, danger zone.
 * Wireframe: admin-panel/_wireframes/data-sources.html. */
export function SourceCardPage() {
  const params = useParams();
  const sourceId = Number(params.sourceId);
  const source = useQuery({
    queryKey: harvesterKeys.source(sourceId),
    queryFn: () => getSource(sourceId),
    staleTime: LIVE_STALE_TIME,
  });

  if (source.isPending) return <PageSkeleton />;
  if (source.isError) return <PageError onRetry={() => void source.refetch()} />;
  return <SourceCard source={source.data} />;
}

function SourceCard({ source }: { source: Source }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [syncOpen, setSyncOpen] = useState(false);
  const syncing = source.health === "syncing";

  const connectors = useQuery({
    queryKey: harvesterKeys.connectors,
    queryFn: listConnectorTypes,
  });
  const connectorTitle =
    connectors.data?.find((item) => item.type === source.connector_type)?.title ??
    source.connector_type;

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: harvesterKeys.source(source.id) });
    void queryClient.invalidateQueries({ queryKey: harvesterKeys.sources });
  };
  const cancel = useMutation({
    mutationFn: () => cancelSync(source.id),
    onSuccess: refresh,
    onError: (error) => void toastApiError(error, t("admin.harvester.syncFailed")),
  });

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
      <BackLink to="/admin/harvester" label={t("admin.nav.harvester")} />
      <header className="flex items-start gap-4">
        <div
          aria-hidden="true"
          className="bg-muted text-muted-foreground grid size-11 shrink-0 place-items-center rounded-xl"
        >
          <DatabaseIcon className="size-5" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
            <h1 className="text-2xl font-semibold tracking-tight">{source.name}</h1>
            <Badge variant={stateBadgeVariant(source.state)}>
              {t(`admin.harvester.states.${source.state}`)}
            </Badge>
            <Badge variant={healthBadgeVariant(source.health)}>
              {syncing && <Spinner className="mr-1 size-3" />}
              {t(`admin.harvester.health.${source.health}`)}
            </Badge>
          </div>
          <p className="text-muted-foreground mt-1 text-sm">
            {connectorTitle} · {t(`admin.harvester.card.accounts.${source.auth_account}`)}
          </p>
        </div>
        <div className="shrink-0">
          {syncing ? (
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                cancel.mutate();
              }}
            >
              {t("admin.harvester.cancelRun")}
            </Button>
          ) : (
            <Button
              size="sm"
              disabled={source.state !== "active"}
              onClick={() => {
                setSyncOpen(true);
              }}
            >
              {t("admin.harvester.sync")}
            </Button>
          )}
        </div>
      </header>
      {syncing && (
        <p className="bg-info/10 text-info flex items-center gap-2 rounded-lg px-3 py-2 text-sm">
          <LockIcon className="size-3.5 shrink-0" aria-hidden="true" />
          {t("admin.harvester.lockedWhileSyncing")}
        </p>
      )}
      <ConfigCard source={source} locked={syncing} />
      <ProbeCard source={source} />
      <ScopeCard source={source} locked={syncing} />
      <ScheduleOverridesCard source={source} locked={syncing} />
      {source.webhook_supported && <WebhookCard source={source} locked={syncing} />}
      <HistoryCard sourceId={source.id} />
      <DangerCard source={source} />
      <SyncDialog sourceId={source.id} open={syncOpen} onOpenChange={setSyncOpen} />
    </div>
  );
}

function ConfigCard({ source, locked }: { source: Source; locked: boolean }) {
  const { t } = useTranslation();
  const connectors = useQuery({
    queryKey: harvesterKeys.connectors,
    queryFn: listConnectorTypes,
  });
  const manifest = connectors.data?.find((item) => item.type === source.connector_type);
  const [credential, setCredential] = useState("");
  const [baseUrl, setBaseUrl] = useState(source.base_url ?? "");
  const baseUrlDirty = baseUrl !== (source.base_url ?? "");

  const save = usePatchSource(source.id);

  return (
    <Card className="shadow-2xs">
      <CardHeader className="border-b">
        <CardTitle className="text-sm font-semibold">{t("admin.harvester.card.config")}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        <dl className="flex flex-col gap-4 text-sm">
          <div className="flex flex-col gap-0.5">
            <dt className="text-muted-foreground text-xs">{t("admin.harvester.card.type")}</dt>
            <dd>{manifest?.title ?? source.connector_type}</dd>
          </div>
          <div className="flex flex-col gap-0.5">
            <dt className="text-muted-foreground text-xs">
              {t("admin.harvester.card.authAccount")}
            </dt>
            <dd>{t(`admin.harvester.card.accounts.${source.auth_account}`)}</dd>
          </div>
        </dl>
        {(manifest?.needs_base_url ?? source.base_url !== null) && (
          <Field label={t("admin.harvester.card.baseUrl")} htmlFor="card-base-url">
            <div className="flex items-center gap-2">
              <Input
                id="card-base-url"
                className="max-w-xs"
                placeholder="https://…"
                value={baseUrl}
                disabled={locked}
                onChange={(event) => {
                  setBaseUrl(event.target.value);
                }}
              />
              <Button
                size="sm"
                disabled={locked || !baseUrlDirty || !baseUrl || save.isPending}
                onClick={() => {
                  save.mutate({ base_url: baseUrl });
                }}
              >
                {t("admin.platform.save")}
              </Button>
            </div>
          </Field>
        )}
        <Field
          label={manifest?.credential_label ?? t("admin.harvester.card.credentialFallback")}
          htmlFor="card-credential"
        >
          <div className="flex items-center gap-2">
            <Input
              id="card-credential"
              type="password"
              className="max-w-xs"
              placeholder={source.credential_is_set ? "••••••••" : ""}
              value={credential}
              disabled={locked}
              onChange={(event) => {
                setCredential(event.target.value);
              }}
            />
            <Button
              size="sm"
              disabled={locked || !credential || save.isPending}
              onClick={() => {
                save.mutate(
                  { credential },
                  {
                    onSuccess: () => {
                      setCredential("");
                    },
                  },
                );
              }}
            >
              {t("admin.platform.save")}
            </Button>
          </div>
        </Field>
      </CardContent>
    </Card>
  );
}

function ProbeCard({ source }: { source: Source }) {
  const { t, i18n } = useTranslation();
  const [diagnosis, setDiagnosis] = useState<Diagnosis | null>(null);
  const probe = useMutation({
    mutationFn: () => testConnection(source.id),
    onSuccess: setDiagnosis,
    onError: (error) => void toastApiError(error, t("admin.harvester.wizard.probeFailed")),
  });

  const probeOk = source.last_probe_status === "ok";

  return (
    <Card className="shadow-2xs">
      <CardHeader className="border-b">
        <CardTitle className="text-sm font-semibold">{t("admin.harvester.card.probe")}</CardTitle>
        <CardAction>
          <Button
            variant="outline"
            size="sm"
            disabled={probe.isPending}
            onClick={() => {
              probe.mutate();
            }}
          >
            {probe.isPending && <Spinner className="mr-1 size-3" />}
            {t("admin.harvester.card.runProbe")}
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent className="flex flex-col gap-2 text-sm">
        {diagnosis ? (
          diagnosis.steps.map((step) => (
            <div key={step.name} className="flex items-center gap-2">
              {step.ok ? (
                <CheckIcon aria-hidden="true" className="text-success size-4 shrink-0" />
              ) : (
                <XIcon aria-hidden="true" className="text-destructive size-4 shrink-0" />
              )}
              <span>{t(`admin.harvester.probeSteps.${step.name}`)}</span>
              {step.detail && <span className="text-muted-foreground text-xs">{step.detail}</span>}
            </div>
          ))
        ) : source.last_probe_status ? (
          <div className="flex items-center gap-2">
            {probeOk ? (
              <CheckIcon aria-hidden="true" className="text-success size-4 shrink-0" />
            ) : (
              <XIcon aria-hidden="true" className="text-destructive size-4 shrink-0" />
            )}
            <span>
              {t("admin.harvester.card.lastProbe", {
                status: source.last_probe_status,
                when: formatWhen(source.last_probe_at, i18n.language) ?? "—",
              })}
            </span>
          </div>
        ) : (
          <p className="text-muted-foreground">{t("admin.harvester.card.notProbed")}</p>
        )}
      </CardContent>
    </Card>
  );
}

function ScopeCard({ source, locked }: { source: Source; locked: boolean }) {
  const { t } = useTranslation();
  const connectors = useQuery({
    queryKey: harvesterKeys.connectors,
    queryFn: listConnectorTypes,
  });
  const toggles = connectors.data?.find(
    (item) => item.type === source.connector_type,
  )?.collection_toggles;

  const save = usePatchSource(source.id);

  return (
    <Card className="shadow-2xs">
      <CardHeader className="border-b">
        <CardTitle className="text-sm font-semibold">{t("admin.harvester.card.scope")}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        <Select
          items={[
            { value: "all", label: t("admin.harvester.scopeAll") },
            { value: "selected", label: t("admin.harvester.scopeSelected") },
          ]}
          value={source.scope_mode}
          disabled={locked}
          onValueChange={(value) => {
            if (value) save.mutate({ scope_mode: value });
          }}
        >
          <SelectTrigger size="sm" className="w-60">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t("admin.harvester.scopeAll")}</SelectItem>
            <SelectItem value="selected">{t("admin.harvester.scopeSelected")}</SelectItem>
          </SelectContent>
        </Select>
        {source.scope_mode === "selected" && <ScopeListEditor source={source} locked={locked} />}
        {(toggles ?? []).length > 0 && (
          <div className="flex flex-col gap-4 border-y py-5">
            {(toggles ?? []).map((toggle) => (
              <SettingRow
                key={toggle}
                label={
                  /* Toggles are declared by connector manifests — unknown ones show raw. */
                  toggle === "include_private" ? (
                    <span className="inline-flex items-center gap-1.5">
                      {t("admin.harvester.card.toggles.include_private")}
                      <InfoHint text={t("admin.harvester.card.builtinFilters")} />
                    </span>
                  ) : (
                    toggle
                  )
                }
                control={
                  <Switch
                    checked={source.content_filters[toggle] ?? true}
                    disabled={locked}
                    onCheckedChange={(checked) => {
                      save.mutate({
                        content_filters: { ...source.content_filters, [toggle]: checked },
                      });
                    }}
                  />
                }
              />
            ))}
          </div>
        )}
        <SettingRow
          label={
            <span className="inline-flex items-center gap-1.5">
              {t("admin.harvester.card.authority")}
              <InfoHint text={t("admin.harvester.card.authorityHint")} />
            </span>
          }
          control={
            <Select
              items={AUTHORITY_TIERS.map((tier) => ({
                value: tier,
                label: t(`admin.harvester.card.tiers.${tier}`),
              }))}
              value={source.authority_tier}
              disabled={locked}
              onValueChange={(value) => {
                if (value) save.mutate({ authority_tier: value });
              }}
            >
              <SelectTrigger size="sm" className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {AUTHORITY_TIERS.map((tier) => (
                  <SelectItem key={tier} value={tier}>
                    {t(`admin.harvester.card.tiers.${tier}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          }
        />
      </CardContent>
    </Card>
  );
}

/** "Selected only" pick from the live catalog (data-sources.html annotation 5):
 * checkboxes + search over GET /catalog, saved as one PATCH scope_list. */
function ScopeListEditor({ source, locked }: { source: Source; locked: boolean }) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<Set<string> | null>(null);
  const [filter, setFilter] = useState("");
  const catalog = useQuery({
    queryKey: harvesterKeys.catalog(source.id),
    queryFn: () => getCatalog(source.id),
    retry: false,
  });
  const save = usePatchSource(source.id);

  const picked = draft ?? new Set(source.scope_list);
  // Live catalog when reachable; otherwise fall back to the saved selection so
  // the chosen containers stay visible even when the source can't be listed.
  const live = catalog.data ?? [];
  const offline = !catalog.isPending && live.length === 0;
  const items: CatalogItem[] = offline
    ? source.scope_list.map((id) => ({ native_id: id, name: id, kind: "" }))
    : live;
  const visible = items.filter((item) => item.name.toLowerCase().includes(filter.toLowerCase()));

  if (catalog.isPending) return <Skeleton className="h-24 w-full max-w-md" />;

  return (
    <div className="flex max-w-md flex-col gap-2">
      <div className="relative">
        <SearchIcon
          aria-hidden="true"
          className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2"
        />
        <Input
          className="pl-9"
          placeholder={t("admin.harvester.wizard.catalogFilter")}
          value={filter}
          onChange={(event) => {
            setFilter(event.target.value);
          }}
        />
      </div>
      {catalog.isError && (
        <p className="text-muted-foreground text-xs">{t("admin.harvester.card.catalogFailed")}</p>
      )}
      <div className="max-h-48 divide-y overflow-y-auto rounded-lg border">
        {visible.length === 0 ? (
          <p className="text-muted-foreground px-3 py-2 text-xs">
            {t("admin.harvester.card.catalogEmpty")}
          </p>
        ) : (
          visible.map((item) => (
            <label
              key={item.native_id}
              className="hover:bg-muted/40 flex items-center gap-2.5 px-3 py-2 text-sm transition-colors"
            >
              <Checkbox
                checked={picked.has(item.native_id)}
                disabled={locked}
                onCheckedChange={(checked) => {
                  const next = new Set(picked);
                  if (checked) next.add(item.native_id);
                  else next.delete(item.native_id);
                  setDraft(next);
                }}
              />
              {item.name}
              {item.kind && <span className="text-muted-foreground text-xs">{item.kind}</span>}
            </label>
          ))
        )}
      </div>
      {draft !== null && (
        <Button
          size="sm"
          className="self-start"
          disabled={locked || picked.size === 0 || save.isPending}
          onClick={() => {
            save.mutate(
              { scope_list: [...picked] },
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
  );
}

function ScheduleOverridesCard({ source, locked }: { source: Source; locked: boolean }) {
  const { t } = useTranslation();
  const save = usePatchSource(source.id);
  const [interval, setInterval] = useState(
    source.sync_interval === null ? "" : String(source.sync_interval),
  );

  return (
    <Card className="shadow-2xs">
      <CardHeader className="border-b">
        <CardTitle className="text-sm font-semibold">
          {t("admin.harvester.card.schedule")}
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-6">
        <div className="flex flex-col gap-3">
          <p className="text-sm font-medium">{t("admin.harvester.card.incremental")}</p>
          <div className="flex items-center justify-between gap-6">
            <p className="text-muted-foreground max-w-96 text-xs">
              {t("admin.harvester.card.ownScheduleHint")}
            </p>
            <Switch
              checked={source.sync_interval !== null}
              disabled={locked}
              onCheckedChange={(checked) => {
                if (!checked) save.mutate({ sync_interval: null });
                else save.mutate({ sync_interval: Number(interval) || 360 });
              }}
            />
          </div>
          {source.sync_interval !== null && (
            <div className="flex items-center gap-2">
              <div className="relative w-28">
                <Input
                  type="number"
                  min={1}
                  className="pr-10"
                  value={interval}
                  disabled={locked}
                  onChange={(event) => {
                    setInterval(event.target.value);
                  }}
                />
                <span className="text-muted-foreground pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-xs">
                  {t("admin.harvester.card.minutes")}
                </span>
              </div>
              <Button
                size="sm"
                disabled={locked || !interval}
                onClick={() => {
                  save.mutate({ sync_interval: Number(interval) });
                }}
              >
                {t("admin.platform.save")}
              </Button>
            </div>
          )}
        </div>
        <ReconcileOverride source={source} locked={locked} />
      </CardContent>
    </Card>
  );
}

/** Per-source reconciliation override (data-sources.html annotation 8): every
 * N days in a weekly run window; off = the global default from the hub. All
 * three fields are edited as one draft and committed together by Save. */
function ReconcileOverride({ source, locked }: { source: Source; locked: boolean }) {
  const { t, i18n } = useTranslation();
  const save = usePatchSource(source.id);
  const overridden = source.reconcile_interval !== null || source.reconcile_window !== null;

  const baseWindow = source.reconcile_window ?? DEFAULT_RECONCILE_WINDOW;
  const base = {
    interval: String(source.reconcile_interval ?? DEFAULT_RECONCILE_INTERVAL_DAYS),
    day: Math.floor(baseWindow / MINUTES_PER_DAY),
    minute: baseWindow % MINUTES_PER_DAY,
  };
  const [draft, setDraft] = useState<typeof base | null>(null);
  const cur = draft ?? base;
  const patch = (next: Partial<typeof base>) => {
    setDraft({ ...cur, ...next });
  };
  const dirty =
    draft !== null &&
    (draft.interval !== base.interval || draft.day !== base.day || draft.minute !== base.minute);

  return (
    <div className="flex flex-col gap-3 border-t pt-6">
      <p className="text-sm font-medium">{t("admin.harvester.card.reconciliation")}</p>
      <div className="flex items-center justify-between gap-6">
        <p className="text-muted-foreground max-w-96 text-xs">
          {t("admin.harvester.card.ownScheduleHint")}
        </p>
        <Switch
          checked={overridden}
          disabled={locked}
          onCheckedChange={(checked) => {
            setDraft(null);
            if (!checked) save.mutate({ reconcile_interval: null, reconcile_window: null });
            else
              save.mutate({
                reconcile_interval: DEFAULT_RECONCILE_INTERVAL_DAYS,
                reconcile_window: DEFAULT_RECONCILE_WINDOW,
              });
          }}
        />
      </div>
      {overridden && (
        <div className="flex w-fit flex-col gap-3">
          <div className="flex items-center gap-2">
            <span className="text-muted-foreground w-28 text-xs">
              {t("admin.harvester.card.reconcileEvery")}
            </span>
            <div className="relative w-24">
              <Input
                type="number"
                min={1}
                className="pr-9"
                value={cur.interval}
                disabled={locked}
                aria-label={t("admin.harvester.card.reconcileEvery")}
                onChange={(event) => {
                  patch({ interval: event.target.value });
                }}
              />
              <span className="text-muted-foreground pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-xs">
                {t("admin.harvester.card.days")}
              </span>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-muted-foreground w-28 text-xs">
              {t("admin.harvester.card.reconcileWhen")}
            </span>
            <Select
              items={WEEKDAYS.map((day) => ({
                value: String(day),
                label: weekdayLong(day, i18n.language),
              }))}
              value={String(cur.day)}
              disabled={locked}
              onValueChange={(value) => {
                if (value) patch({ day: Number(value) });
              }}
            >
              <SelectTrigger size="sm" className="w-36">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {WEEKDAYS.map((day) => (
                  <SelectItem key={day} value={String(day)}>
                    {weekdayLong(day, i18n.language)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <TimeOfDayInput
              key={minuteToTime(cur.minute)}
              value={minuteToTime(cur.minute)}
              label={t("admin.harvester.reconcileTime")}
              disabled={locked}
              onCommit={(minute) => {
                patch({ minute });
              }}
            />
          </div>
          <div className="flex justify-end">
            <Button
              size="sm"
              disabled={locked || !cur.interval || !dirty || save.isPending}
              onClick={() => {
                save.mutate(
                  {
                    reconcile_interval: Number(cur.interval) || DEFAULT_RECONCILE_INTERVAL_DAYS,
                    reconcile_window: cur.day * MINUTES_PER_DAY + cur.minute,
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
          </div>
        </div>
      )}
    </div>
  );
}

/** Real-time webhook channel (data-sources.html#webhooks): a toggle, the
 * endpoint to paste into the source, and the signing secret with rotation. */
function WebhookCard({ source, locked }: { source: Source; locked: boolean }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const save = usePatchSource(source.id);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [freshSecret, setFreshSecret] = useState<string | null>(null);

  const copy = (value: string) => {
    void navigator.clipboard.writeText(value).then(
      () => toast.success(t("admin.harvester.webhooks.copied")),
      () => toast.error(t("admin.harvester.webhooks.copyFailed")),
    );
  };
  const rotate = useMutation({
    mutationFn: () => rotateWebhookSecret(source.id),
    onSuccess: (result) => {
      setFreshSecret(result.secret);
      void queryClient.invalidateQueries({ queryKey: harvesterKeys.source(source.id) });
    },
    onError: (error) => void toastApiError(error, t("admin.platform.saveFailed")),
  });

  return (
    <Card className="shadow-2xs">
      <CardHeader className="border-b">
        <CardTitle className="text-sm font-semibold">
          {t("admin.harvester.webhooks.title")}
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-5 text-sm">
        <div className="border-b pb-5">
          <SettingRow
            label={t("admin.harvester.webhooks.realtime")}
            hint={source.webhook_secret_set ? undefined : t("admin.harvester.webhooks.needsSecret")}
            control={
              <Switch
                aria-label={t("admin.harvester.webhooks.realtime")}
                checked={source.webhook_enabled}
                disabled={locked || save.isPending || !source.webhook_secret_set}
                onCheckedChange={(next) => {
                  save.mutate({ webhook_enabled: next });
                }}
              />
            }
          />
        </div>

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="webhook-endpoint" className="inline-flex items-center gap-1.5">
            {t("admin.harvester.webhooks.endpoint")}
            <InfoHint text={t("admin.harvester.webhooks.endpointHint")} />
          </Label>
          <div className="flex items-center gap-2">
            <Input
              id="webhook-endpoint"
              readOnly
              className="max-w-md font-mono text-xs"
              value={source.webhook_endpoint_url ?? ""}
            />
            <Button
              variant="outline"
              size="icon-sm"
              aria-label={t("admin.harvester.webhooks.copy")}
              disabled={!source.webhook_endpoint_url}
              onClick={() => {
                if (source.webhook_endpoint_url) copy(source.webhook_endpoint_url);
              }}
            >
              <CopyIcon className="size-4" />
            </Button>
          </div>
        </div>

        <div className="flex flex-col gap-1.5">
          <Label>{t("admin.harvester.webhooks.secret")}</Label>
          {freshSecret ? (
            <div className="border-success/40 bg-success/5 flex flex-col gap-1.5 rounded-lg border p-3">
              <p className="text-muted-foreground text-xs">
                {t("admin.harvester.webhooks.freshHint")}
              </p>
              <div className="flex items-center gap-2">
                <Input readOnly className="max-w-xs font-mono text-xs" value={freshSecret} />
                <Button
                  variant="outline"
                  size="icon-sm"
                  aria-label={t("admin.harvester.webhooks.copy")}
                  onClick={() => {
                    copy(freshSecret);
                  }}
                >
                  <CopyIcon className="size-4" />
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex items-center gap-3">
              <span className="text-muted-foreground flex items-center gap-1.5 font-mono text-xs">
                <LockIcon className="size-3.5" aria-hidden="true" />
                {source.webhook_secret_set
                  ? "••••••••••••"
                  : t("admin.harvester.webhooks.noSecret")}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={locked || rotate.isPending}
                onClick={() => {
                  setConfirmOpen(true);
                }}
              >
                {source.webhook_secret_set
                  ? t("admin.harvester.webhooks.rotate")
                  : t("admin.harvester.webhooks.generate")}
              </Button>
            </div>
          )}
        </div>

        <ConfirmDialog
          open={confirmOpen}
          onOpenChange={setConfirmOpen}
          title={t("admin.harvester.webhooks.rotateTitle")}
          description={t("admin.harvester.webhooks.rotateHint")}
          confirmLabel={t("admin.harvester.webhooks.rotateConfirm")}
          pending={rotate.isPending}
          onConfirm={() => {
            rotate.mutate();
          }}
        />
      </CardContent>
    </Card>
  );
}

/** SyncMode → label key; unknown modes fall back to incremental wording. */
function modeKey(mode: string): "full" | "incremental" | "reconciliation" {
  if (mode === "full" || mode === "reconciliation") return mode;
  return "incremental";
}

function HistoryCard({ sourceId }: { sourceId: number }) {
  const { t, i18n } = useTranslation();
  const [open, setOpen] = useState(false);
  const runs = useQuery({
    queryKey: harvesterKeys.runs(sourceId),
    queryFn: () => listRuns(sourceId),
    enabled: open,
    staleTime: LIVE_STALE_TIME,
  });

  return (
    <Card className="shadow-2xs">
      <CardHeader className={open ? "items-center border-b" : "items-center"}>
        <CardTitle className="text-sm font-semibold">{t("admin.harvester.card.history")}</CardTitle>
        <CardAction className="row-span-1 self-center">
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setOpen((value) => !value);
            }}
          >
            {open ? t("admin.harvester.card.fold") : t("admin.harvester.card.unfold")}
          </Button>
        </CardAction>
      </CardHeader>
      {open && (
        <CardContent>
          {runs.isPending ? (
            <Skeleton className="h-16 w-full" />
          ) : (
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="text-muted-foreground text-xs font-medium">
                    {t("admin.harvester.card.runStarted")}
                  </TableHead>
                  <TableHead className="text-muted-foreground text-xs font-medium">
                    {t("admin.harvester.card.runMode")}
                  </TableHead>
                  <TableHead className="text-muted-foreground text-xs font-medium">
                    {t("admin.harvester.card.runDuration")}
                  </TableHead>
                  <TableHead className="text-muted-foreground text-xs font-medium">
                    {t("admin.harvester.card.runEntities")}
                  </TableHead>
                  <TableHead className="text-muted-foreground text-xs font-medium">
                    {t("admin.harvester.card.runOutcome")}
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(runs.data ?? []).map((run) => (
                  <TableRow key={run.id} className="hover:bg-muted/40 h-12">
                    <TableCell className="tabular-nums">
                      {formatWhen(run.started_at ?? run.created_at, i18n.language)}
                    </TableCell>
                    <TableCell>
                      {t(`admin.harvester.card.modes.${modeKey(run.mode)}`)}
                      {(RUN_TRIGGERS as readonly string[]).includes(run.trigger) && (
                        <span className="text-muted-foreground ml-1.5 text-xs">
                          {t(
                            `admin.harvester.card.triggers.${run.trigger as (typeof RUN_TRIGGERS)[number]}`,
                          )}
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="text-muted-foreground tabular-nums">
                      {formatDurationBetween(run.started_at, run.finished_at, i18n.language) ?? "—"}
                    </TableCell>
                    <TableCell className="tabular-nums">
                      {run.entities_done ?? "—"}
                      {run.entities_total !== null && ` / ${String(run.entities_total)}`}
                    </TableCell>
                    <TableCell>
                      <Badge variant={runStateBadgeVariant(run.state)}>
                        {runStateLabel(run.state, t)}
                      </Badge>
                      {run.error_count > 0 && (
                        <span className="text-muted-foreground ml-2 text-xs">
                          {t("admin.harvester.dlqPill", { count: run.error_count })}
                        </span>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      )}
    </Card>
  );
}

function DangerCard({ source }: { source: Source }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [typed, setTyped] = useState("");

  const pause = usePatchSource(source.id);
  const remove = useMutation({
    mutationFn: () => deleteSource(source.id, typed),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: harvesterKeys.sources });
      toast.success(t("admin.harvester.card.deleted"));
      void navigate("/admin/harvester");
    },
    onError: (error) => void toastApiError(error, t("admin.harvester.card.deleteFailed")),
  });

  return (
    <Card className="shadow-2xs">
      <CardHeader className="items-center">
        <CardTitle className="text-sm font-semibold">{t("admin.harvester.card.manage")}</CardTitle>
        <CardAction className="row-span-1 flex gap-2 self-center">
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              pause.mutate({ state: source.state === "paused" ? "active" : "paused" });
            }}
          >
            {source.state === "paused" ? t("admin.harvester.resume") : t("admin.harvester.pause")}
          </Button>
          <Button
            variant="destructive"
            size="sm"
            onClick={() => {
              setTyped("");
              setConfirmOpen(true);
            }}
          >
            {t("admin.harvester.card.delete")}
          </Button>
        </CardAction>
      </CardHeader>
      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("admin.harvester.card.delete")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("admin.harvester.card.deleteHint", { name: source.name })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="delete-confirm-name">{t("admin.harvester.wizard.name")}</Label>
            <Input
              id="delete-confirm-name"
              value={typed}
              onChange={(event) => {
                setTyped(event.target.value);
              }}
            />
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("admin.platform.cancel")}</AlertDialogCancel>
            <AlertDialogAction
              disabled={typed !== source.name || remove.isPending}
              onClick={() => {
                remove.mutate();
              }}
            >
              {t("admin.harvester.card.delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  );
}
