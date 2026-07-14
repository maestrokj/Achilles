import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BoxesIcon,
  ChevronDownIcon,
  LayersIcon,
  PlugIcon,
  PlusIcon,
  SlidersHorizontalIcon,
  TriangleAlertIcon,
  XIcon,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "@/lib/toast";

import { toastApiError } from "@/api/errors";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { InfoHint } from "@/components/InfoHint";
import { TruncatedText } from "@/components/TruncatedText";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  DataTable,
  SortableHead,
  TableFrame,
  TruncateCell,
} from "@/components/list-controls/DataTable";
import { EmptyState } from "@/components/list-controls/EmptyState";
import { RowActions } from "@/components/list-controls/RowActions";
import { useClientSort, type SortAccessors } from "@/components/list-controls/useClientSort";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { chatQueryKeys } from "@/features/chat/api";
import { formatBytes, formatPrice } from "@/lib/format";
import { useHashTarget } from "@/lib/useHashTarget";

import {
  aiKeys,
  checkProvider,
  checkProviderConfig,
  createModel,
  createProvider,
  deleteModel,
  deleteProvider,
  getAssignments,
  listModels,
  listProviders,
  patchAssignments,
  patchModel,
  patchProvider,
  providerDiscovery,
} from "./api";
import {
  PRESET_GROUP_ORDER,
  PROVIDER_PRESETS,
  presetHidesBaseUrl,
  presetRequiresKey,
} from "./providerPresets";
import type {
  AiModel,
  CheckStatus,
  Discovery,
  ModelList,
  ModelListItem,
  ModelType,
  Provider,
} from "./types";
import { useEmbedderPhase } from "./useEmbedderPhase";

const MODEL_TYPES: ModelType[] = ["chat", "embedding"];
/** How many discovered ("found at provider") rows show before the rest fold
 * behind a "show more" toggle — a freshly connected cloud can report dozens. */
const FOUND_COLLAPSED_COUNT = 5;
/** Remembers the last provider whose catalogue the admin looked at, so a return
 * trip lands on the same one instead of resetting to the first row. */
const PROVIDER_STORAGE_KEY = "achilles.aiModels.provider";

function readStoredProvider(): number | null {
  const raw = localStorage.getItem(PROVIDER_STORAGE_KEY);
  const id = raw === null ? NaN : Number(raw);
  return Number.isInteger(id) ? id : null;
}

/** Anchor of the catalogue card — the empty assignment selects point here. */
const CATALOG_ANCHOR = "ai-catalog";
/** Anchor of the assignments card — cross-page deep-links (e.g. Harvester's
 * embedder pointer, /admin/ai-models#assignments) land and pulse here. */
const ASSIGNMENTS_ANCHOR = "assignments";

type ProviderSortKey = "name" | "kind" | "status";
const PROVIDER_SORT: SortAccessors<Provider, ProviderSortKey> = {
  name: (p) => p.name.toLowerCase(),
  kind: (p) => p.kind,
  status: (p) => p.status,
};

/** Display order for the provider pickers: real providers alphabetically, the
 * built-in (system) provider sunk to the bottom — the catalogue targets a real
 * provider by default, so the system one is never the first thing offered. */
function byProviderDisplay(a: Provider, b: Provider): number {
  if (a.is_system !== b.is_system) return a.is_system ? 1 : -1;
  return a.name.localeCompare(b.name, undefined, { sensitivity: "base" });
}

/** Calm status dot — success/error read semantically, unchecked stays neutral. */
const STATUS_DOT: Record<CheckStatus, string> = {
  active: "bg-success",
  error: "bg-destructive",
  unchecked: "bg-muted-foreground/40",
};

function StatusChip({ status }: { status: CheckStatus }) {
  const { t } = useTranslation();
  return (
    <span className="text-muted-foreground flex items-center gap-1.5 text-xs">
      <span aria-hidden="true" className={`size-1.5 rounded-full ${STATUS_DOT[status]}`} />
      {t(`admin.aiModels.statuses.${status}`)}
    </span>
  );
}

/** Section header — tinted icon disc + title + description over a hairline, the
 * warm-minimalism card chrome shared across the admin surface. */
function SectionHead({
  icon: Icon,
  title,
  titleAside,
  description,
  action,
}: {
  icon: LucideIcon;
  title: string;
  titleAside?: ReactNode;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <CardHeader className="border-b">
      <CardTitle className="flex items-center gap-2.5 text-sm font-semibold">
        <span
          aria-hidden="true"
          className="bg-secondary text-muted-foreground grid size-7 shrink-0 place-items-center rounded-lg"
        >
          <Icon className="size-4" />
        </span>
        {title}
        {titleAside}
      </CardTitle>
      {description && (
        <CardDescription className="mt-1.5 max-w-prose text-xs leading-relaxed">
          {description}
        </CardDescription>
      )}
      {action && <CardAction className="self-center">{action}</CardAction>}
    </CardHeader>
  );
}

/** Admin · AI models: providers, per-provider catalogue, function assignments.
 * Wireframe: admin-panel/_wireframes/ai-models.html. */
export function AiModelsPage() {
  const { t } = useTranslation();
  useHashTarget();
  // The catalogue's provider choice lives here so that a freshly created
  // provider can select itself (and, being first-touched, auto-discover); it is
  // seeded from (and written back to) localStorage so the choice survives a reload.
  const [providerId, setProviderId] = useState<number | null>(readStoredProvider);
  const selectProvider = (id: number) => {
    localStorage.setItem(PROVIDER_STORAGE_KEY, String(id));
    setProviderId(id);
  };
  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
      <h1 className="text-2xl font-semibold tracking-tight">{t("admin.nav.aiModels")}</h1>
      <ProvidersCard onCreated={selectProvider} />
      <CatalogCard providerId={providerId} onProviderChange={selectProvider} />
      <AssignmentsCard />
    </div>
  );
}

function useAiInvalidate() {
  const queryClient = useQueryClient();
  // The chat picker's allow-list (GET /chat/models) is a projection of this AI
  // state — the enabled chat models and the default. Any edit here (enabling a
  // model/provider, moving the default) must refresh it too, or an admin who
  // opened chat in the same session sees a stale picker for the 5-min staleTime.
  return () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ["admin", "ai"] }),
      queryClient.invalidateQueries({ queryKey: chatQueryKeys.models }),
    ]);
}

function ProvidersCard({ onCreated }: { onCreated: (providerId: number) => void }) {
  const { t } = useTranslation();
  const invalidate = useAiInvalidate();
  const providers = useQuery({ queryKey: aiKeys.providers, queryFn: listProviders });
  const [editing, setEditing] = useState<Provider | "new" | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<Provider | null>(null);
  // Few providers per org — sorting is client-side, by name ascending by default.
  const {
    sorted,
    sort,
    toggle: toggleSort,
  } = useClientSort(providers.data ?? [], PROVIDER_SORT, { key: "name", desc: false });

  const check = useMutation({
    mutationFn: checkProvider,
    onSuccess: (verdict) => {
      if (verdict.status === "active") toast.success(t("admin.aiModels.checkOk"));
      else toast.error(t("admin.aiModels.checkFailed"));
      void invalidate();
    },
    onError: (error) => void toastApiError(error, t("admin.aiModels.checkFailed")),
  });
  const remove = useMutation({
    mutationFn: deleteProvider,
    onSuccess: () => void invalidate(),
    onError: (error) => void toastApiError(error, t("admin.aiModels.deleteBlocked")),
  });

  return (
    <Card id="ai-providers" className="scroll-mt-6 shadow-2xs">
      <SectionHead
        icon={PlugIcon}
        title={t("admin.aiModels.providers")}
        action={
          <Button
            size="sm"
            onClick={() => {
              setEditing("new");
            }}
          >
            {t("admin.aiModels.addProvider")}
          </Button>
        }
      />
      <CardContent>
        {providers.isPending ? (
          <Skeleton className="h-24 w-full" />
        ) : providers.isError ? (
          <EmptyState
            variant="error"
            description={t("common.list.errorTitle")}
            onRetry={() => {
              void providers.refetch();
            }}
          />
        ) : (
          <TableFrame variant="card">
            <DataTable>
              <TableHeader>
                <TableRow>
                  <SortableHead
                    label={t("admin.aiModels.table.provider")}
                    sortKey="name"
                    sort={sort}
                    onToggle={toggleSort}
                  />
                  <SortableHead
                    label={t("admin.aiModels.table.type")}
                    sortKey="kind"
                    sort={sort}
                    onToggle={toggleSort}
                  />
                  <TableHead>{t("admin.aiModels.table.endpoint")}</TableHead>
                  <TableHead>{t("admin.aiModels.table.key")}</TableHead>
                  <SortableHead
                    label={t("admin.aiModels.table.status")}
                    sortKey="status"
                    sort={sort}
                    onToggle={toggleSort}
                  />
                  <TableHead className="w-10" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {sorted.map((provider) => (
                  <TableRow key={provider.id} className="hover:bg-muted/40">
                    <TableCell className="max-w-[16rem]">
                      <div className="flex flex-col gap-0.5">
                        <span className="truncate font-medium" title={provider.name}>
                          {provider.name}
                        </span>
                        <span className="text-muted-foreground truncate text-xs">
                          {provider.adapter}
                        </span>
                      </div>
                    </TableCell>
                    <TableCell className="text-muted-foreground text-sm">
                      {t(`admin.aiModels.kinds.${provider.kind}`)}
                    </TableCell>
                    <TruncateCell
                      className="text-muted-foreground max-w-[20rem] text-xs"
                      text={provider.base_url ?? "—"}
                    />
                    <TruncateCell className="max-w-[14rem]" text={provider.api_key_mask ?? "—"}>
                      <code className="text-muted-foreground text-xs">
                        {provider.api_key_mask ?? "—"}
                      </code>
                    </TruncateCell>
                    <TableCell>
                      <StatusChip status={provider.status} />
                    </TableCell>
                    <TableCell className="text-right">
                      <RowActions
                        actions={[
                          {
                            label: t("admin.aiModels.check"),
                            onSelect: () => {
                              check.mutate(provider.id);
                            },
                            disabled: check.isPending,
                          },
                          {
                            label: t("admin.aiModels.edit"),
                            onSelect: () => {
                              setEditing(provider);
                            },
                            hidden: provider.is_system,
                          },
                          {
                            label: t("admin.aiModels.delete"),
                            onSelect: () => {
                              setConfirmDelete(provider);
                            },
                            disabled: remove.isPending,
                            destructive: true,
                            hidden: provider.is_system,
                          },
                        ]}
                      />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </DataTable>
          </TableFrame>
        )}
      </CardContent>
      {editing !== null && (
        <ProviderDialog
          provider={editing === "new" ? null : editing}
          onCreated={onCreated}
          onClose={() => {
            setEditing(null);
          }}
        />
      )}
      <ConfirmDialog
        open={confirmDelete !== null}
        onOpenChange={(open) => {
          if (!open) setConfirmDelete(null);
        }}
        title={t("admin.aiModels.deleteConfirmTitle", { name: confirmDelete?.name ?? "" })}
        description={t("admin.aiModels.deleteConfirmBody")}
        confirmLabel={t("admin.aiModels.delete")}
        onConfirm={() => {
          if (confirmDelete) remove.mutate(confirmDelete.id);
          setConfirmDelete(null);
        }}
      />
    </Card>
  );
}

function ProviderDialog({
  provider,
  onCreated,
  onClose,
}: {
  provider: Provider | null;
  onCreated: (providerId: number) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const invalidate = useAiInvalidate();
  // A new provider is picked from the preset catalogue; kind/adapter and the
  // default base URL ride the preset. Editing keeps the stored adapter fixed.
  const [presetId, setPresetId] = useState(PROVIDER_PRESETS[0].id);
  const preset = PROVIDER_PRESETS.find((p) => p.id === presetId) ?? PROVIDER_PRESETS[0];
  const [name, setName] = useState(provider?.name ?? PROVIDER_PRESETS[0].label);
  const [baseUrl, setBaseUrl] = useState(provider?.base_url ?? PROVIDER_PRESETS[0].baseUrl ?? "");
  const [apiKey, setApiKey] = useState("");

  // Switching preset resets the endpoint and, while the name is still the
  // vendor default, keeps it in step so the admin rarely types it by hand.
  const pickPreset = (id: string) => {
    const next = PROVIDER_PRESETS.find((p) => p.id === id);
    if (!next) return;
    setName((cur) => (cur.trim() === "" || cur === preset.label ? next.label : cur));
    setBaseUrl(next.baseUrl ?? "");
    setPresetId(id);
  };

  // Editing always shows the endpoint (the adapter is fixed); a new provider
  // hides it for native-SDK clouds and requires it for everything else.
  const showBaseUrl = provider ? true : !presetHidesBaseUrl(preset);
  const keyRequired = provider ? false : presetRequiresKey(preset);
  const baseUrlMissing = !provider && showBaseUrl && !baseUrl.trim();
  const keyMissing = keyRequired && !apiKey.trim();

  const save = useMutation({
    mutationFn: () =>
      provider
        ? patchProvider(provider.id, {
            name,
            base_url: baseUrl || null,
            ...(apiKey ? { api_key: apiKey } : {}),
          })
        : createProvider({
            name,
            kind: preset.kind,
            adapter: preset.adapter,
            base_url: baseUrl || null,
            api_key: apiKey || null,
          }),
    onSuccess: (saved) => {
      void invalidate();
      if (!provider) onCreated(saved.id);
      onClose();
    },
    onError: (error) => void toastApiError(error, t("admin.aiModels.saveFailed")),
  });

  // Probe the entered credentials before anything is written (create only —
  // an existing provider has the row-level "Check" button).
  const checkConfig = useMutation({
    mutationFn: () =>
      checkProviderConfig({
        kind: preset.kind,
        adapter: preset.adapter,
        base_url: baseUrl || null,
        api_key: apiKey || null,
      }),
    onSuccess: (verdict) => {
      if (verdict.status === "active") toast.success(t("admin.aiModels.checkOk"));
      else toast.error(t("admin.aiModels.checkFailed"));
    },
    onError: (error) => void toastApiError(error, t("admin.aiModels.checkFailed")),
  });

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent className="max-w-sm p-6">
        <DialogHeader>
          <DialogTitle>
            {provider ? t("admin.aiModels.editProvider") : t("admin.aiModels.addProvider")}
          </DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          {!provider && (
            <div className="flex flex-col gap-1.5">
              <Label>{t("admin.aiModels.fields.preset")}</Label>
              <Select
                value={presetId}
                onValueChange={(value) => {
                  if (value) pickPreset(value);
                }}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent className="w-auto min-w-[22rem]">
                  {PRESET_GROUP_ORDER.map((group) => (
                    <SelectGroup key={group}>
                      <SelectLabel>{t(`admin.aiModels.presetGroups.${group}`)}</SelectLabel>
                      {PROVIDER_PRESETS.filter((p) => p.group === group).map((p) => (
                        <SelectItem key={p.id} value={p.id}>
                          {p.labelKey ? t(p.labelKey) : p.label}
                        </SelectItem>
                      ))}
                    </SelectGroup>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="p-name">{t("admin.aiModels.fields.name")}</Label>
            <Input
              id="p-name"
              value={name}
              onChange={(e) => {
                setName(e.target.value);
              }}
            />
          </div>
          {showBaseUrl && (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="p-url">{t("admin.aiModels.fields.baseUrl")}</Label>
              <Input
                id="p-url"
                value={baseUrl}
                placeholder={preset.placeholder ?? "https://…"}
                onChange={(e) => {
                  setBaseUrl(e.target.value);
                }}
              />
            </div>
          )}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="p-key">
              {t("admin.aiModels.fields.apiKey")}
              {!keyRequired && !provider && (
                <span className="text-muted-foreground ml-1.5 text-xs font-normal">
                  {t("admin.aiModels.fields.optional")}
                </span>
              )}
            </Label>
            <Input
              id="p-key"
              type="password"
              value={apiKey}
              placeholder={provider?.api_key_mask ?? ""}
              onChange={(e) => {
                setApiKey(e.target.value);
              }}
            />
            <p className="text-muted-foreground text-xs">{t("admin.aiModels.keyWriteOnly")}</p>
          </div>
        </div>
        <DialogFooter className="-mx-6 -mb-6 px-6 py-4">
          {!provider && (
            <Button
              variant="outline"
              disabled={!name.trim() || baseUrlMissing || checkConfig.isPending}
              onClick={() => {
                checkConfig.mutate();
              }}
            >
              {t("admin.aiModels.checkConnection")}
            </Button>
          )}
          <Button
            disabled={!name.trim() || baseUrlMissing || keyMissing || save.isPending}
            onClick={() => {
              save.mutate();
            }}
          >
            {t("admin.platform.save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function CatalogCard({
  providerId,
  onProviderChange,
}: {
  providerId: number | null;
  onProviderChange: (providerId: number) => void;
}) {
  const { t } = useTranslation();
  const invalidate = useAiInvalidate();
  const providers = useQuery({ queryKey: aiKeys.providers, queryFn: listProviders });
  const models = useQuery({ queryKey: aiKeys.models, queryFn: listModels });
  const [manualOpen, setManualOpen] = useState(false);
  // Per-visit discovery cache: provider id → the models it reported. Local (not
  // the query cache), so it resets on every screen open — a fresh visit
  // re-discovers, a return trip within the visit reuses what was found.
  // `attempted` guards the single automatic run per provider so a silent failure
  // never loops; the manual "Discover" button ignores it and always re-runs.
  const [discovered, setDiscovered] = useState<Record<number, Discovery["models"]>>({});
  const attempted = useRef<Set<number>>(new Set());
  // Per-visit, per-provider "found list expanded?" — same lifetime as `discovered`
  // above: a return trip to the same provider restores its choice, but leaving
  // and re-entering the screen unmounts this and everything folds shut again.
  const [foundExpanded, setFoundExpanded] = useState<Record<number, boolean>>({});

  // A stored provider that no longer exists (deleted since) falls back to the
  // first non-system provider, then the first row.
  const remembered =
    providerId !== null && providers.data?.some((p) => p.id === providerId)
      ? providerId
      : undefined;
  const selected =
    remembered ?? providers.data?.find((p) => !p.is_system)?.id ?? providers.data?.[0]?.id;
  // Same order as the providers table above: alphabetical, system provider last.
  const providerOptions = [...(providers.data ?? [])].sort(byProviderDisplay);
  const discovery = useMutation({
    mutationFn: (v: { id: number; manual: boolean }) => providerDiscovery(v.id),
    onSuccess: (data, v) => {
      setDiscovered((prev) => ({ ...prev, [v.id]: data.models }));
    },
    // Automatic first-touch runs fail quietly (the found list just stays empty);
    // only an explicit refresh reports the error.
    onError: (error, v) => {
      if (v.manual) void toastApiError(error, t("admin.aiModels.discoveryFailed"));
    },
  });
  const { mutate: runDiscovery } = discovery;

  // First time a real (non-system) provider is shown this visit, pull its
  // catalogue automatically; the system provider has no upstream to discover.
  useEffect(() => {
    if (selected === undefined || attempted.current.has(selected)) return;
    const provider = providers.data?.find((p) => p.id === selected);
    if (!provider || provider.is_system) return;
    attempted.current.add(selected);
    runDiscovery({ id: selected, manual: false });
  }, [selected, providers.data, runDiscovery]);

  const add = useMutation({
    mutationFn: (body: {
      model_id: string;
      display_name?: string;
      model_type: ModelType;
      origin: "discovered" | "manual";
      price_input?: string | null;
      price_output?: string | null;
      meta?: Record<string, unknown> | null;
    }) => createModel({ provider_id: selected ?? 0, ...body }),
    onSuccess: () => void invalidate(),
    onError: (error) => void toastApiError(error, t("admin.aiModels.saveFailed")),
  });

  // A calm alphabetical order by display name — stable under the inline edits
  // (toggle, type, price) that never move a row out from under the cursor.
  const catalogue = (models.data ?? [])
    .filter((m) => m.provider_id === selected)
    .sort((a, b) =>
      a.display_name.localeCompare(b.display_name, undefined, { sensitivity: "base" }),
    );
  const known = new Set(catalogue.map((m) => m.model_id));

  const found = (selected !== undefined ? (discovered[selected] ?? []) : [])
    .filter((m) => !known.has(m.model_id))
    .sort((a, b) =>
      (a.display_name ?? a.model_id).localeCompare(b.display_name ?? b.model_id, undefined, {
        sensitivity: "base",
      }),
    );
  // Fold a long found list to the first few; the toggle only appears when there
  // is something to fold. State is keyed by provider, so switching away and back
  // lands on the same open/closed view.
  const expanded = selected !== undefined && (foundExpanded[selected] ?? false);
  const visibleFound = expanded ? found : found.slice(0, FOUND_COLLAPSED_COUNT);
  const foldable = found.length > FOUND_COLLAPSED_COUNT;
  const toggleFound = () => {
    if (selected !== undefined)
      setFoundExpanded((prev) => ({ ...prev, [selected]: !prev[selected] }));
  };

  return (
    <Card id={CATALOG_ANCHOR} className="scroll-mt-6 shadow-2xs">
      <SectionHead
        icon={LayersIcon}
        title={t("admin.aiModels.catalog")}
        titleAside={
          <Select
            items={providerOptions.map((provider) => ({
              value: String(provider.id),
              label: provider.name,
            }))}
            value={selected === undefined ? "" : String(selected)}
            onValueChange={(value) => {
              if (value) onProviderChange(Number(value));
            }}
          >
            <SelectTrigger size="sm" className="ml-1 w-44 font-normal">
              <SelectValue />
            </SelectTrigger>
            <SelectContent className="w-auto max-w-[min(24rem,var(--available-width))] min-w-44">
              {providerOptions.map((provider) => (
                <SelectItem key={provider.id} value={String(provider.id)}>
                  {provider.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        }
        action={
          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={selected === undefined || discovery.isPending}
              onClick={() => {
                if (selected !== undefined) runDiscovery({ id: selected, manual: true });
              }}
            >
              {t("admin.aiModels.discover")}
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={selected === undefined}
              onClick={() => {
                setManualOpen(true);
              }}
            >
              {t("admin.aiModels.addModel")}
            </Button>
          </div>
        }
      />
      <CardContent className="flex flex-col gap-4">
        {catalogue.length > 0 ? (
          <div className="divide-border divide-y overflow-hidden rounded-lg border">
            {catalogue.map((model) => (
              <ModelRow key={model.id} model={model} />
            ))}
          </div>
        ) : (
          <EmptyState icon={BoxesIcon} description={t("admin.aiModels.noModels")} />
        )}
        {found.length > 0 && (
          <div className="flex flex-col gap-1.5">
            <span className="text-muted-foreground text-xs font-medium">
              {t("admin.aiModels.foundAtProvider")}
            </span>
            <div className="divide-border divide-y overflow-hidden rounded-lg border border-dashed">
              {visibleFound.map((model) => (
                <div
                  key={model.model_id}
                  className="flex min-h-11 items-center gap-3 px-3 py-2 text-sm"
                >
                  <TruncatedText className="font-medium">
                    {model.display_name ?? model.model_id}
                  </TruncatedText>
                  <Badge variant="outline">{model.model_type ?? "chat"}</Badge>
                  <Button
                    variant="ghost"
                    size="xs"
                    className="text-primary ml-auto"
                    disabled={add.isPending}
                    onClick={() => {
                      add.mutate({
                        model_id: model.model_id,
                        display_name: model.display_name ?? undefined,
                        model_type: model.model_type ?? "chat",
                        origin: "discovered",
                      });
                    }}
                  >
                    {t("admin.aiModels.activate")}
                  </Button>
                </div>
              ))}
              {foldable && (
                <button
                  type="button"
                  onClick={toggleFound}
                  className="text-muted-foreground hover:text-foreground hover:bg-muted/40 flex min-h-11 w-full items-center justify-center gap-1.5 px-3 py-2 text-xs font-medium transition-colors"
                >
                  {expanded
                    ? t("admin.aiModels.foundShowLess")
                    : t("admin.aiModels.foundShowMore", {
                        count: found.length - FOUND_COLLAPSED_COUNT,
                      })}
                  <ChevronDownIcon
                    className={`size-3.5 transition-transform ${expanded ? "rotate-180" : ""}`}
                  />
                </button>
              )}
            </div>
          </div>
        )}
      </CardContent>
      {manualOpen && (
        <ManualModelDialog
          onAdd={(body) => {
            add.mutate({ ...body, origin: "manual" });
          }}
          onClose={() => {
            setManualOpen(false);
          }}
        />
      )}
    </Card>
  );
}

/** "Add model ID" — providers without discovery and fine-tuned models. Prices
 * can be set right here (input always; output only for chat), so a hand-added
 * model starts counting spend without a second trip through the edit dialog. */
function ManualModelDialog({
  onAdd,
  onClose,
}: {
  onAdd: (body: {
    model_id: string;
    display_name?: string;
    model_type: ModelType;
    price_input?: string | null;
    price_output?: string | null;
    meta?: Record<string, unknown> | null;
  }) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [modelId, setModelId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [modelType, setModelType] = useState<ModelType>("chat");
  const [priceInput, setPriceInput] = useState("");
  const [priceOutput, setPriceOutput] = useState("");
  const [dimensions, setDimensions] = useState("");

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent className="max-w-sm p-6">
        <DialogHeader>
          <DialogTitle>{t("admin.aiModels.addModel")}</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="m-id">{t("admin.aiModels.fields.modelId")}</Label>
            <Input
              id="m-id"
              value={modelId}
              onChange={(e) => {
                setModelId(e.target.value);
              }}
            />
            <p className="text-muted-foreground text-xs">
              {t("admin.aiModels.fields.modelIdHint")}
            </p>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="m-name">{t("admin.aiModels.fields.name")}</Label>
            <Input
              id="m-name"
              value={displayName}
              placeholder={modelId}
              onChange={(e) => {
                setDisplayName(e.target.value);
              }}
            />
            <p className="text-muted-foreground text-xs">
              {t("admin.aiModels.fields.displayNameHint")}
            </p>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>{t("admin.aiModels.fields.type")}</Label>
            <Select
              value={modelType}
              onValueChange={(value) => {
                if (value) setModelType(value);
              }}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {MODEL_TYPES.map((value) => (
                  <SelectItem key={value} value={value}>
                    {t(`admin.aiModels.types.${value}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {modelType === "embedding" && (
            <DimensionField id="m-dim" value={dimensions} onChange={setDimensions} />
          )}
          <div className="flex flex-col gap-1.5">
            <Label className="flex items-center gap-1.5">
              {t("admin.aiModels.fields.pricing")}
              <InfoHint text={t("admin.aiModels.fields.pricingHint")} />
            </Label>
            <div className="flex items-end gap-3">
              <PriceInput
                id="m-price-input"
                label={t("admin.aiModels.fields.priceInput")}
                value={priceInput}
                onChange={setPriceInput}
              />
              {modelType === "chat" && (
                <PriceInput
                  id="m-price-output"
                  label={t("admin.aiModels.fields.priceOutput")}
                  value={priceOutput}
                  onChange={setPriceOutput}
                />
              )}
            </div>
            <p className="text-muted-foreground text-xs">
              {t("admin.aiModels.fields.pricePerMillion")}
            </p>
          </div>
        </div>
        <DialogFooter className="-mx-6 -mb-6 px-6 py-4">
          <Button
            disabled={!modelId.trim()}
            onClick={() => {
              const dim = dimensions.trim();
              onAdd({
                model_id: modelId.trim(),
                display_name: displayName.trim() || undefined,
                model_type: modelType,
                price_input: priceInput.trim() || null,
                price_output: modelType === "chat" ? priceOutput.trim() || null : null,
                meta: modelType === "embedding" && dim ? { embedding_dim: Number(dim) } : undefined,
              });
              onClose();
            }}
          >
            {t("admin.aiModels.add")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Approximate on-disk/in-memory footprint of a model, or null when unknown.
 * Only the built-in embedders carry it (seeded in ai_models.meta); cloud models
 * have no local size, so their picker entry stays bare. */
function modelSizeLabel(model: AiModel): string | null {
  const bytes = model.meta?.approx_size_bytes;
  return typeof bytes === "number" ? `~${formatBytes(bytes)}` : null;
}

/** The embedding width declared in a model's intrinsics, or null when undeclared.
 * Built-in embedders are seeded with it; a cloud model carries it only once the
 * admin enters it (discovery doesn't report it). Drives the assignment gate. */
function modelDim(model: AiModel): number | null {
  const dim = model.meta?.embedding_dim;
  return typeof dim === "number" ? dim : null;
}

/** Compact per-1M price for a priced catalogue row: "$in / $out" for chat, "$in"
 * for embedding; a missing side shows an em dash. */
function modelPriceLabel(model: AiModel, locale: string): string {
  const side = (value: string | null) => (value === null ? "—" : formatPrice(value, locale));
  return model.model_type === "chat"
    ? `${side(model.price_input)} / ${side(model.price_output)}`
    : side(model.price_input);
}

function ModelRow({ model }: { model: AiModel }) {
  const { t, i18n } = useTranslation();
  const invalidate = useAiInvalidate();
  // Local runtimes (the built-in embedder) have no $/token, so they carry no
  // price affordance. A priceable model shows its price, or an inviting "set
  // price" so a fresh catalogue of dashes never leaves the admin guessing.
  const priceable = model.origin !== "builtin";
  const hasPrice = model.price_input !== null || model.price_output !== null;
  const dim = modelDim(model);
  const [confirmOff, setConfirmOff] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [editing, setEditing] = useState(false);

  const toggle = useMutation({
    mutationFn: (enabled: boolean) => patchModel(model.id, { is_enabled: enabled }),
    onSuccess: () => void invalidate(),
    onError: (error) => void toastApiError(error, t("admin.aiModels.saveFailed")),
  });
  const remove = useMutation({
    mutationFn: () => deleteModel(model.id),
    onSuccess: () => void invalidate(),
    onError: (error) => void toastApiError(error, t("admin.aiModels.deleteModelFailed")),
  });

  return (
    <div className="hover:bg-muted/30 flex min-h-12 items-center gap-3 px-3 py-2 text-sm transition-colors">
      <Switch
        checked={model.is_enabled}
        onCheckedChange={(enabled) => {
          // Turning a model off may strand assignments — ask first.
          if (enabled) toggle.mutate(true);
          else setConfirmOff(true);
        }}
      />
      <div className="flex min-w-0 flex-col">
        <span className="font-medium">{model.display_name}</span>
        {model.model_id !== model.display_name && (
          <code className="text-muted-foreground text-xs">{model.model_id}</code>
        )}
      </div>
      {model.model_type === "embedding" && dim !== null && (
        <Badge
          variant="outline"
          className="font-normal tabular-nums"
          title={t("admin.aiModels.dimTitle")}
        >
          {t("admin.aiModels.dimBadge", { dim })}
        </Badge>
      )}
      {priceable && (
        // Both "set …" affordances share the right edge so a bare model offers
        // its dimension and price side by side, not split across the row.
        <div className="ml-auto flex items-center gap-2">
          {model.model_type === "embedding" && dim === null && (
            <Button
              variant="ghost"
              size="xs"
              className="text-primary"
              onClick={() => {
                setEditing(true);
              }}
            >
              {t("admin.aiModels.setDimension")}
            </Button>
          )}
          {hasPrice ? (
            <span
              className="text-muted-foreground text-xs whitespace-nowrap tabular-nums"
              title={t("admin.aiModels.fields.pricePerMillion")}
            >
              {modelPriceLabel(model, i18n.language)}
            </span>
          ) : (
            <Button
              variant="ghost"
              size="xs"
              className="text-primary"
              onClick={() => {
                setEditing(true);
              }}
            >
              {t("admin.aiModels.setPrice")}
            </Button>
          )}
        </div>
      )}
      <div className={`flex items-center gap-2 ${priceable ? "" : "ml-auto"}`}>
        <TypeSelect model={model} />
        <RowActions
          actions={[
            {
              label: t("admin.aiModels.edit"),
              onSelect: () => {
                setEditing(true);
              },
            },
            {
              label: t("admin.aiModels.delete"),
              onSelect: () => {
                setConfirmDelete(true);
              },
              disabled: remove.isPending,
              destructive: true,
              // The built-in embedder ships with the platform — it is not deletable.
              hidden: model.origin === "builtin",
            },
          ]}
        />
      </div>
      {editing && (
        <ModelEditDialog
          model={model}
          onClose={() => {
            setEditing(false);
          }}
        />
      )}
      <ConfirmDialog
        open={confirmOff}
        onOpenChange={setConfirmOff}
        title={t("admin.aiModels.disableConfirmTitle", { name: model.display_name })}
        description={t("admin.aiModels.disableConfirmBody")}
        confirmLabel={t("admin.aiModels.disable")}
        onConfirm={() => {
          toggle.mutate(false);
          setConfirmOff(false);
        }}
      />
      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title={t("admin.aiModels.deleteModelConfirmTitle", { name: model.display_name })}
        description={t("admin.aiModels.deleteModelConfirmBody")}
        confirmLabel={t("admin.aiModels.delete")}
        onConfirm={() => {
          remove.mutate();
          setConfirmDelete(false);
        }}
      />
    </div>
  );
}

/** A $-prefixed decimal field for a per-1M-token price. Empty string clears the
 * price; the wrapper matches the token/budget adornment used across the surface. */
function PriceInput({
  id,
  label,
  value,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="flex flex-1 flex-col gap-1.5">
      <Label htmlFor={id} className="text-muted-foreground text-xs font-normal">
        {label}
      </Label>
      <div className="relative">
        <span className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-xs">
          $
        </span>
        <Input
          id={id}
          type="number"
          min={0}
          step="any"
          className="pl-7"
          value={value}
          placeholder="—"
          onChange={(event) => {
            onChange(event.target.value);
          }}
        />
      </div>
    </div>
  );
}

/** Embedding width field — an integer intrinsic entered by hand because discovery
 * doesn't report it. Only shown for the embedding type; the assignment gate checks
 * it against the knowledge base's provisioned width. */
function DimensionField({
  id,
  value,
  onChange,
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{t("admin.aiModels.fields.dimensions")}</Label>
      <Input
        id={id}
        type="number"
        min={1}
        step={1}
        value={value}
        placeholder="1024"
        onChange={(event) => {
          onChange(event.target.value);
        }}
      />
      <p className="text-muted-foreground text-xs">{t("admin.aiModels.fields.dimensionsHint")}</p>
    </div>
  );
}

/** Edit a catalogue model — the provider-side ID is fixed (read-only); the
 * display name and the per-token prices are editable (PATCH). The type has its
 * own inline select, so it is not repeated here. Local runtimes (the built-in
 * embedder) have no $/token, so their pricing block is hidden; embedding models
 * bill on input only, so their output price is omitted. */
function ModelEditDialog({ model, onClose }: { model: AiModel; onClose: () => void }) {
  const { t } = useTranslation();
  const invalidate = useAiInvalidate();
  const [name, setName] = useState(model.display_name);
  const [priceInput, setPriceInput] = useState(model.price_input ?? "");
  const [priceOutput, setPriceOutput] = useState(model.price_output ?? "");
  const [dimensions, setDimensions] = useState(modelDim(model)?.toString() ?? "");

  const showPricing = model.origin !== "builtin";
  const showOutput = model.model_type === "chat";
  // A cloud embedder's width is entered here; the built-in ones come pre-declared.
  const showDim = model.model_type === "embedding" && model.origin !== "builtin";

  const save = useMutation({
    mutationFn: () =>
      patchModel(model.id, {
        display_name: name.trim(),
        ...(showPricing
          ? {
              price_input: priceInput.trim() || null,
              price_output: showOutput ? priceOutput.trim() || null : null,
            }
          : {}),
        ...(showDim && dimensions.trim()
          ? { meta: { embedding_dim: Number(dimensions.trim()) } }
          : {}),
      }),
    onSuccess: () => {
      void invalidate();
      onClose();
    },
    onError: (error) => void toastApiError(error, t("admin.aiModels.saveFailed")),
  });

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent className="max-w-sm p-6">
        <DialogHeader>
          <DialogTitle>{t("admin.aiModels.editModelTitle")}</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="edit-model-id">{t("admin.aiModels.fields.modelId")}</Label>
            <Input id="edit-model-id" value={model.model_id} readOnly disabled />
            <p className="text-muted-foreground text-xs">{t("admin.aiModels.modelIdReadonly")}</p>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="edit-model-name">{t("admin.aiModels.fields.name")}</Label>
            <Input
              id="edit-model-name"
              value={name}
              onChange={(event) => {
                setName(event.target.value);
              }}
            />
          </div>
          {showDim && (
            <DimensionField id="edit-model-dim" value={dimensions} onChange={setDimensions} />
          )}
          {showPricing && (
            <div className="flex flex-col gap-1.5">
              <Label className="flex items-center gap-1.5">
                {t("admin.aiModels.fields.pricing")}
                <InfoHint text={t("admin.aiModels.fields.pricingHint")} />
              </Label>
              <div className="flex items-end gap-3">
                <PriceInput
                  id="edit-price-input"
                  label={t("admin.aiModels.fields.priceInput")}
                  value={priceInput}
                  onChange={setPriceInput}
                />
                {showOutput && (
                  <PriceInput
                    id="edit-price-output"
                    label={t("admin.aiModels.fields.priceOutput")}
                    value={priceOutput}
                    onChange={setPriceOutput}
                  />
                )}
              </div>
              <p className="text-muted-foreground text-xs">
                {t("admin.aiModels.fields.pricePerMillion")}
              </p>
            </div>
          )}
        </div>
        <DialogFooter className="-mx-6 -mb-6 px-6 py-4">
          <Button
            disabled={!name.trim() || save.isPending}
            onClick={() => {
              save.mutate();
            }}
          >
            {t("admin.platform.save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Inline "what is this model for" — chat vs embeddings. Re-typing a model that
 * is in use is refused (409 MODEL_IN_USE) and surfaces "reassign it first". */
function TypeSelect({ model }: { model: AiModel }) {
  const { t } = useTranslation();
  const invalidate = useAiInvalidate();

  const change = useMutation({
    mutationFn: (model_type: ModelType) => patchModel(model.id, { model_type }),
    onSuccess: () => void invalidate(),
    onError: (error) => void toastApiError(error, t("admin.aiModels.saveFailed")),
  });

  return (
    <Select
      value={model.model_type}
      onValueChange={(value) => {
        if (value && value !== model.model_type) change.mutate(value);
      }}
    >
      <SelectTrigger size="sm" className="w-36" aria-label={t("admin.aiModels.changeType")}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {MODEL_TYPES.map((value) => (
          <SelectItem key={value} value={value}>
            {t(`admin.aiModels.types.${value}`)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function AssignmentsCard() {
  const { t } = useTranslation();
  const invalidate = useAiInvalidate();
  const queryClient = useQueryClient();
  const assignments = useQuery({ queryKey: aiKeys.assignments, queryFn: getAssignments });
  const models = useQuery({ queryKey: aiKeys.models, queryFn: listModels });
  const providers = useQuery({ queryKey: aiKeys.providers, queryFn: listProviders });
  const { phase, percent, reembed, error: runtimeError } = useEmbedderPhase();
  // The embedding switch is confirmed first — a full re-embed is heavy.
  const [pendingEmbedding, setPendingEmbedding] = useState<number | null>(null);

  const save = useMutation({
    mutationFn: patchAssignments,
    onSuccess: () => {
      toast.success(t("admin.aiModels.assignmentsSaved"));
      void invalidate();
      void queryClient.invalidateQueries({ queryKey: ["admin", "knowledge"] });
    },
    onError: (error) => void toastApiError(error, t("admin.aiModels.saveFailed")),
  });

  if (assignments.isPending || models.isPending) return <Skeleton className="h-40 w-full" />;
  if (assignments.isError || models.isError)
    return (
      <EmptyState
        variant="error"
        description={t("common.list.errorTitle")}
        onRetry={() => {
          void assignments.refetch();
          void models.refetch();
        }}
      />
    );

  const enabled = models.data.filter((m) => m.is_enabled);
  const embedders = enabled.filter((m) => m.model_type === "embedding");
  const chats = enabled.filter((m) => m.model_type === "chat");
  const data = assignments.data;

  const byId = new Map(models.data.map((m) => [m.id, m]));
  const providerName = (providerId: number) =>
    providers.data?.find((p) => p.id === providerId)?.name;
  const pendingName =
    pendingEmbedding === null ? "" : (byId.get(pendingEmbedding)?.display_name ?? "");
  const currentEmbeddingName =
    data.harvester_embedding === null
      ? t("admin.aiModels.notAssigned")
      : (byId.get(data.harvester_embedding)?.display_name ?? "");

  return (
    <Card id={ASSIGNMENTS_ANCHOR} className="scroll-mt-6 shadow-2xs">
      <SectionHead icon={SlidersHorizontalIcon} title={t("admin.aiModels.assignments")} />
      <CardContent className="flex flex-col gap-6">
        <AssignRow
          label={t("admin.aiModels.functions.embedding")}
          labelHint={t("admin.aiModels.functions.embeddingHint")}
          value={data.harvester_embedding}
          options={embedders}
          // Only a model whose width matches the provisioned column can be
          // assigned; the rest stay listed but disabled with the reason.
          requiredDim={data.embedding_dim}
          // Weights loading keeps the select open (picking again just
          // supersedes the load); only a live re-embed run locks it.
          disabled={phase === "reembedding"}
          onPick={(id) => {
            setPendingEmbedding(id);
          }}
          chip={
            phase === "loading" ? (
              <Badge variant="warning">
                <Spinner className="border-warning/40 border-t-warning size-3" />
                {t("admin.aiModels.weightsLoading")}
              </Badge>
            ) : phase === "reembedding" ? (
              <span className="flex items-center gap-2">
                <Badge variant="warning">
                  {t("admin.aiModels.reembedRunning", { percent: percent ?? 0 })}
                </Badge>
                {reembed?.from_model != null && reembed.to_model != null && (
                  <span className="text-muted-foreground text-xs">
                    {reembed.from_model} → {reembed.to_model}
                  </span>
                )}
              </span>
            ) : phase === "error" ? (
              <span className="flex items-center gap-2">
                <Badge variant="destructive">{t("admin.aiModels.runtimeError")}</Badge>
                {runtimeError != null && (
                  <span className="text-muted-foreground max-w-md truncate text-xs">
                    {runtimeError}
                  </span>
                )}
              </span>
            ) : phase === "offline" ? (
              <Badge variant="secondary">{t("admin.aiModels.runtimeOffline")}</Badge>
            ) : null
          }
        />
        <div className="bg-border h-px" />
        <ModelBoard
          label={t("admin.aiModels.functions.chat")}
          hint={t("admin.aiModels.functions.chatHint")}
          list={data.chat_models}
          options={chats}
          providerName={providerName}
          onSave={(chat_models) => {
            save.mutate({ chat_models });
          }}
        />
        <div className="bg-border h-px" />
        <ModelBoard
          label={t("admin.aiModels.functions.agents")}
          hint={t("admin.aiModels.functions.agentsHint")}
          list={data.agent_models}
          options={chats}
          providerName={providerName}
          onSave={(agent_models) => {
            save.mutate({ agent_models });
          }}
        />
      </CardContent>
      <AlertDialog
        open={pendingEmbedding !== null}
        onOpenChange={(open) => {
          if (!open) setPendingEmbedding(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("admin.aiModels.reembedTitle")}</AlertDialogTitle>
            <AlertDialogDescription>{t("admin.aiModels.reembedWarning")}</AlertDialogDescription>
          </AlertDialogHeader>
          <p className="text-sm">
            <span className="text-muted-foreground">{currentEmbeddingName}</span>
            {" → "}
            <span className="font-medium">{pendingName}</span>
          </p>
          <p className="text-muted-foreground text-xs">{t("admin.aiModels.reembedNote")}</p>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("admin.platform.cancel")}</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (pendingEmbedding !== null)
                  save.mutate({ harvester_embedding: pendingEmbedding });
                setPendingEmbedding(null);
              }}
            >
              {t("admin.aiModels.reembedConfirm")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  );
}

/** One function → one model. Empty options render a pointer to the catalogue.
 * `labelHint` rides an info icon beside the label; `hint` is a bottom line. */
function AssignRow({
  label,
  labelHint,
  hint,
  value,
  options,
  requiredDim,
  disabled = false,
  onPick,
  chip,
}: {
  label: string;
  labelHint?: ReactNode;
  hint?: ReactNode;
  value: number | null;
  options: AiModel[];
  /** When set, an option whose embedding width differs (or is undeclared) is
   * shown disabled with the reason — the knowledge base column is fixed to it. */
  requiredDim?: number;
  disabled?: boolean;
  onPick: (id: number) => void;
  chip?: ReactNode;
}) {
  const { t } = useTranslation();
  // Fold each option into a compatibility verdict once, then split so the
  // pickable models lead and the wrong-width ones settle under their own
  // heading — the admin sees what they *can* choose before the noise of what
  // they can't.
  const opts = options.map((model) => {
    const dim = modelDim(model);
    // Resolve the reason inline so `requiredDim` narrows to a number in the
    // interpolation; a non-null reason *is* the incompatibility verdict.
    const reason =
      requiredDim != null && dim !== requiredDim
        ? dim === null
          ? t("admin.aiModels.dimNotSet")
          : t("admin.aiModels.dimNeeds", { dim, required: requiredDim })
        : null;
    return { model, dim, size: modelSizeLabel(model), reason };
  });
  const compatible = opts.filter((o) => o.reason === null);
  const blocked = opts.filter((o) => o.reason !== null);
  const gated = blocked.length > 0;

  const option = ({ model, dim, size, reason }: (typeof opts)[number]) => (
    <SelectItem
      key={model.id}
      value={String(model.id)}
      disabled={reason !== null}
      className="py-1.5"
    >
      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="truncate leading-tight font-medium">{model.display_name}</span>
        <span className="text-muted-foreground flex items-center gap-1.5 text-xs leading-tight">
          {reason !== null ? (
            <>
              <TriangleAlertIcon className="size-3 shrink-0" />
              {reason}
            </>
          ) : (
            <>
              {dim !== null && (
                <span className="tabular-nums">{t("admin.aiModels.dimBadge", { dim })}</span>
              )}
              {dim !== null && size && <span className="text-muted-foreground/40">·</span>}
              {size && (
                <span className="tabular-nums" title={t("admin.aiModels.approxSize")}>
                  {size}
                </span>
              )}
            </>
          )}
        </span>
      </span>
    </SelectItem>
  );

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between gap-3">
        <Label className="flex items-center gap-1.5">
          {label}
          {labelHint && <InfoHint text={labelHint} />}
        </Label>
        {options.length === 0 ? (
          <p className="text-muted-foreground text-sm">
            {t("admin.aiModels.noModelsOfType")}{" "}
            <a href={`#${CATALOG_ANCHOR}`} className="text-primary hover:underline">
              {t("admin.aiModels.goToCatalog")}
            </a>
          </p>
        ) : (
          <div className="flex items-center gap-3">
            {chip}
            <Select
              items={options.map((model) => ({
                value: String(model.id),
                label: model.display_name,
              }))}
              value={value === null ? "" : String(value)}
              disabled={disabled}
              onValueChange={(next) => {
                if (next && Number(next) !== value) onPick(Number(next));
              }}
            >
              <SelectTrigger className="w-64">
                <SelectValue placeholder={t("admin.aiModels.notAssigned")} />
              </SelectTrigger>
              <SelectContent align="start" className="w-[22rem] max-w-[calc(100vw-2rem)]">
                {gated && requiredDim != null ? (
                  <>
                    {compatible.length > 0 && (
                      <SelectGroup>
                        <SelectLabel>
                          {t("admin.aiModels.dimGroupOk", { dim: requiredDim })}
                        </SelectLabel>
                        {compatible.map(option)}
                      </SelectGroup>
                    )}
                    <SelectGroup>
                      <SelectLabel>{t("admin.aiModels.dimGroupBlocked")}</SelectLabel>
                      {blocked.map(option)}
                    </SelectGroup>
                  </>
                ) : (
                  opts.map(option)
                )}
              </SelectContent>
            </Select>
          </div>
        )}
      </div>
      {hint && <p className="text-muted-foreground max-w-96 text-xs">{hint}</p>}
    </div>
  );
}

/** A curated allow-list board (chat / agents): only the models the admin added,
 * each with a pause toggle, a default mark, and remove; "Add model" pulls from
 * the enabled catalogue not yet on the board — so hundreds of catalogue models
 * never flood the screen. The default always rides a live (present + enabled)
 * entry; toggling or removing it hands the mark to the next enabled model. */
function ModelBoard({
  label,
  hint,
  list,
  options,
  providerName,
  onSave,
}: {
  label: string;
  hint: string;
  list: ModelList;
  options: AiModel[];
  providerName: (providerId: number) => string | undefined;
  onSave: (list: { items: ModelListItem[]; default: number | null }) => void;
}) {
  const { t } = useTranslation();
  const [confirmRemove, setConfirmRemove] = useState<AiModel | null>(null);

  const byId = new Map(options.map((m) => [m.id, m]));
  // Render in list order; drop any id the catalogue no longer offers.
  const rows = list.items.flatMap((item) => {
    const model = byId.get(item.id);
    return model ? [{ item, model }] : [];
  });
  const inList = new Set(list.items.map((item) => item.id));
  const available = options.filter((m) => !inList.has(m.id));

  // Persist the whole board; keep the default on a live entry or move it on.
  const settle = (items: ModelListItem[], preferred: number | null) => {
    const enabledIds = items.filter((it) => it.is_enabled).map((it) => it.id);
    const next =
      preferred !== null && enabledIds.includes(preferred) ? preferred : (enabledIds[0] ?? null);
    onSave({ items, default: next });
  };
  const add = (id: number) => {
    settle([...list.items, { id, is_enabled: true }], list.default);
  };
  const toggle = (id: number, on: boolean) => {
    settle(
      list.items.map((it) => (it.id === id ? { ...it, is_enabled: on } : it)),
      list.default,
    );
  };
  const removeModel = (id: number) => {
    settle(
      list.items.filter((it) => it.id !== id),
      list.default,
    );
  };

  return (
    <div className="flex flex-col gap-2.5">
      <Label className="flex items-center gap-1.5">
        {label}
        <InfoHint text={hint} />
      </Label>
      {options.length === 0 ? (
        <p className="text-muted-foreground text-sm">
          {t("admin.aiModels.noModelsOfType")}{" "}
          <a href={`#${CATALOG_ANCHOR}`} className="text-primary hover:underline">
            {t("admin.aiModels.goToCatalog")}
          </a>
        </p>
      ) : (
        <div className="flex flex-col gap-2">
          {rows.length > 0 && (
            <div className="divide-border divide-y overflow-hidden rounded-lg border">
              {rows.map(({ item, model }) => {
                const isDefault = list.default === model.id;
                const provider = providerName(model.provider_id);
                return (
                  <div
                    key={model.id}
                    className="flex min-h-12 items-center gap-3 px-3 py-2 text-sm"
                  >
                    <Switch
                      checked={item.is_enabled}
                      aria-label={t("admin.aiModels.enabledToggle")}
                      onCheckedChange={(on) => {
                        toggle(model.id, on);
                      }}
                    />
                    <div className="flex min-w-0 items-center gap-2">
                      <span
                        className={
                          item.is_enabled ? "font-medium" : "text-muted-foreground line-through"
                        }
                      >
                        {model.display_name}
                      </span>
                      {provider && (
                        <Badge variant="outline" className="font-normal">
                          {provider}
                        </Badge>
                      )}
                    </div>
                    <div className="ml-auto flex items-center gap-1.5">
                      {isDefault ? (
                        <Badge variant="secondary">{t("admin.aiModels.default")}</Badge>
                      ) : (
                        item.is_enabled && (
                          <Button
                            variant="ghost"
                            size="xs"
                            onClick={() => {
                              settle(list.items, model.id);
                            }}
                          >
                            {t("admin.aiModels.makeDefault")}
                          </Button>
                        )
                      )}
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={t("admin.aiModels.remove")}
                        className="text-muted-foreground hover:text-destructive"
                        onClick={() => {
                          setConfirmRemove(model);
                        }}
                      >
                        <XIcon className="size-4" />
                      </Button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
          <AddModelControl available={available} providerName={providerName} onAdd={add} />
        </div>
      )}
      <ConfirmDialog
        open={confirmRemove !== null}
        onOpenChange={(open) => {
          if (!open) setConfirmRemove(null);
        }}
        title={t("admin.aiModels.removeConfirmTitle", { name: confirmRemove?.display_name ?? "" })}
        description={t("admin.aiModels.removeConfirmBody")}
        confirmLabel={t("admin.aiModels.remove")}
        onConfirm={() => {
          if (confirmRemove) removeModel(confirmRemove.id);
          setConfirmRemove(null);
        }}
      />
    </div>
  );
}

/** "Add model" affordance for a board — a compact picker of the enabled chat
 * models not yet on the list; picking one adds it. Each option carries its
 * provider as a muted badge (right-aligned) so same-named models from different
 * providers stay distinguishable. Hidden pool → a calm note. */
function AddModelControl({
  available,
  providerName,
  onAdd,
}: {
  available: AiModel[];
  providerName: (providerId: number) => string | undefined;
  onAdd: (id: number) => void;
}) {
  const { t } = useTranslation();
  if (available.length === 0) {
    return <p className="text-muted-foreground text-xs">{t("admin.aiModels.allAdded")}</p>;
  }
  return (
    <Select
      value=""
      onValueChange={(value) => {
        if (value) onAdd(Number(value));
      }}
    >
      <SelectTrigger size="sm" className="text-primary w-fit gap-1.5 font-normal">
        <PlusIcon className="size-3.5" />
        <SelectValue placeholder={t("admin.aiModels.addToList")} />
      </SelectTrigger>
      <SelectContent className="w-[18rem] max-w-[calc(100vw-2rem)]">
        {available.map((model) => {
          const provider = providerName(model.provider_id);
          return (
            <SelectItem key={model.id} value={String(model.id)} className="pr-1.5">
              <span className="min-w-0 flex-1 truncate">{model.display_name}</span>
              {provider && (
                <Badge variant="outline" className="ml-auto shrink-0 font-normal">
                  {provider}
                </Badge>
              )}
            </SelectItem>
          );
        })}
      </SelectContent>
    </Select>
  );
}
