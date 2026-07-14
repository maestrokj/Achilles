import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BlocksIcon,
  Code2Icon,
  DatabaseIcon,
  GlobeIcon,
  KeyRoundIcon,
  LinkIcon,
  Share2Icon,
  SigmaIcon,
  WrenchIcon,
  type LucideIcon,
} from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "@/lib/toast";

import type { TFunction } from "i18next";

import { toastApiError } from "@/api/errors";
import { BuildYourOwnCard } from "@/components/BuildYourOwnCard";
import { ComingSoonCard } from "@/components/ComingSoonCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
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
import { formatDateTime } from "@/lib/format";

import { aiKeys, checkTool, createTool, listTools, patchTool } from "./api";
import type { CheckStatus, Tool } from "./types";

/** Admin · AI tools: the open catalogue; each tool lives on two surfaces
 * (chat / agents) toggled separately. Wireframe: admin-panel/_wireframes/tools.html. */
export function ToolsPage() {
  const { t } = useTranslation();
  const tools = useQuery({ queryKey: aiKeys.tools, queryFn: listTools });

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
      <h1 className="text-2xl font-semibold tracking-tight">{t("admin.nav.aiTools")}</h1>
      {tools.isPending ? (
        <div className="flex flex-col gap-4">
          <Skeleton className="h-32 w-full rounded-xl" />
          <Skeleton className="h-32 w-full rounded-xl" />
        </div>
      ) : tools.isError ? (
        <EmptyState
          variant="error"
          description={t("common.list.errorTitle")}
          onRetry={() => {
            void tools.refetch();
          }}
        />
      ) : (
        <div className="flex flex-col gap-4">
          {tools.data.map((tool) => (
            <ToolCard key={tool.name} tool={tool} />
          ))}
        </div>
      )}
      <BuildYourOwnCard
        icon={Code2Icon}
        title={t("admin.aiTools.custom.title")}
        description={t("admin.aiTools.custom.desc")}
        doc="ai-foundation/_workzone/tool-catalog.html#custom"
      />
      <ComingSoonCard
        icon={BlocksIcon}
        title={t("admin.aiTools.mcpTitle")}
        note={t("admin.aiTools.mcpSoon")}
      />
    </div>
  );
}

/** Per-tool glyph — a calm visual anchor so a stack of tools reads at a glance
 * rather than as a wall of near-identical rows. Unknown tools get a wrench. */
const TOOL_ICONS: Record<string, LucideIcon> = {
  web_search: GlobeIcon,
  fetch_url: LinkIcon,
  search_knowledge: DatabaseIcon,
  search: DatabaseIcon,
  graph: Share2Icon,
  sql: SigmaIcon,
};

const TOOL_NAME_KEYS = [
  "web_search",
  "fetch_url",
  "search_knowledge",
  "search",
  "graph",
  "sql",
] as const;
const TOOL_ACCESS_KEYS = ["read_only", "read_write"] as const;

/** Search engines behind the single web_search schema —
 * mirrors backend ai_foundation/tools/web_search.py. */
const WEB_SEARCH_PROVIDERS: Record<string, string> = {
  tavily: "Tavily",
  brave: "Brave",
  serper: "Serper",
  google_cse: "Google CSE",
};

/** Catalog identifiers → human labels; unknown tokens fall back to the raw value. */
function toolNameLabel(name: string, t: TFunction): string {
  return (TOOL_NAME_KEYS as readonly string[]).includes(name)
    ? t(`admin.aiTools.names.${name as (typeof TOOL_NAME_KEYS)[number]}`)
    : name;
}

function toolAccessLabel(access: string, t: TFunction): string {
  return (TOOL_ACCESS_KEYS as readonly string[]).includes(access)
    ? t(`admin.aiTools.access.${access as (typeof TOOL_ACCESS_KEYS)[number]}`)
    : access;
}

/** "checked · N min ago" for a fresh probe, an absolute stamp once it is stale. */
function agoLabel(iso: string, locale: string, t: TFunction): string {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60_000));
  return minutes < 60 ? t("admin.aiTools.minutesAgo", { minutes }) : formatDateTime(iso, locale);
}

/** A registered type may have no instance row yet (id null) —
 * the first write materializes it via POST before PATCH can address it. */
function ensureToolId(tool: Tool) {
  return async (): Promise<number> => {
    if (tool.id !== null) return tool.id;
    const created = await createTool({ name: tool.name });
    if (created.id === null) throw new Error(`tool ${tool.name} was created without an id`);
    return created.id;
  };
}

/** Calm health signal — a semantic dot plus "working · N min ago", mirroring
 * the status-dot idiom used across the admin surface. Hidden until first probe. */
function ToolStatus({
  status,
  at,
}: {
  status: Exclude<CheckStatus, "unchecked">;
  at: string | null;
}) {
  const { t, i18n } = useTranslation();
  return (
    <span className="text-muted-foreground flex shrink-0 items-center gap-1.5 text-xs whitespace-nowrap">
      <span
        aria-hidden="true"
        className={`size-1.5 rounded-full ${status === "active" ? "bg-success" : "bg-destructive"}`}
      />
      {t(`admin.aiTools.statusBadge.${status}`)}
      {at ? ` · ${agoLabel(at, i18n.language, t)}` : ""}
    </span>
  );
}

/** A switch with its label, wrapped so the label names the control. */
function ToggleField({
  label,
  checked,
  disabled,
  onChange,
}: {
  label: string;
  checked: boolean;
  disabled: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className="flex items-center gap-2 text-sm">
      <Switch checked={checked} disabled={disabled} onCheckedChange={onChange} />
      <span className={disabled ? "text-muted-foreground" : ""}>{label}</span>
    </label>
  );
}

function ToolCard({ tool }: { tool: Tool }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const ensureId = ensureToolId(tool);
  const core = tool.source === "core" && tool.name.startsWith("search");
  const invalidate = () => queryClient.invalidateQueries({ queryKey: aiKeys.tools });

  const toggle = useMutation({
    mutationFn: async (body: { chat_enabled?: boolean; agents_allowed?: boolean }) =>
      patchTool(await ensureId(), body),
    onSuccess: () => void invalidate(),
    onError: (error) => void toastApiError(error, t("admin.platform.saveFailed")),
  });

  const check = useMutation({
    mutationFn: async () => checkTool(await ensureId()),
    onSuccess: (verdict) => {
      if (verdict.status === "active") toast.success(t("admin.aiTools.checkPassed"));
      else toast.error(t("admin.aiTools.checkFailed"));
      void invalidate();
    },
    onError: (error) => void toastApiError(error, t("admin.aiTools.checkFailed")),
  });

  const provider = typeof tool.config?.["provider"] === "string" ? tool.config["provider"] : null;
  const Icon = TOOL_ICONS[tool.name] ?? WrenchIcon;
  const label = toolNameLabel(tool.name, t);
  // Hide the raw id when it is already the visible label (custom tools).
  const showRaw = label !== tool.name;

  return (
    <Card className="gap-0 py-0 shadow-2xs transition-shadow hover:shadow-sm">
      {/* Identity — glyph, human name over its raw id, access, and health. */}
      <div className="flex items-start gap-3.5 px-5 pt-4 pb-3.5">
        <span
          aria-hidden="true"
          className="bg-secondary text-muted-foreground grid size-9 shrink-0 place-items-center rounded-lg"
        >
          <Icon className="size-[1.15rem]" strokeWidth={1.75} />
        </span>
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="font-heading text-foreground text-sm leading-snug font-medium">
              {label}
            </span>
            <Badge variant="outline" className="font-normal">
              {toolAccessLabel(tool.access, t)}
            </Badge>
          </div>
          {showRaw && <code className="text-muted-foreground/70 text-xs">{tool.name}</code>}
          {core && (
            <span className="text-muted-foreground text-xs leading-relaxed">
              {t("admin.aiTools.core")}
            </span>
          )}
        </div>
        {/* Right rail — the health signal, with the credential state beneath it. */}
        <div className="flex shrink-0 flex-col items-end gap-1.5">
          {tool.status !== "unchecked" && (
            <ToolStatus status={tool.status} at={tool.last_check_at} />
          )}
          {!core && tool.needs_credential && (
            <span className="flex items-center gap-1.5 text-xs">
              <KeyRoundIcon
                aria-hidden="true"
                className="text-muted-foreground/60 size-3.5 shrink-0"
              />
              <span className={tool.credential_is_set ? "text-muted-foreground" : "text-warning"}>
                {provider !== null && `${WEB_SEARCH_PROVIDERS[provider] ?? provider} · `}
                {tool.credential_is_set ? t("admin.aiTools.keySet") : t("admin.aiTools.keyMissing")}
              </span>
            </span>
          )}
        </div>
      </div>
      {/* Controls — the two independent routes, with per-tool actions trailing. */}
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3 border-t px-5 py-3">
        <ToggleField
          label={t("admin.aiTools.inChat")}
          checked={tool.chat_enabled}
          disabled={toggle.isPending || core}
          onChange={(chat_enabled) => {
            toggle.mutate({ chat_enabled });
          }}
        />
        <ToggleField
          label={t("admin.aiTools.forAgents")}
          checked={tool.agents_allowed}
          disabled={toggle.isPending || core}
          onChange={(agents_allowed) => {
            toggle.mutate({ agents_allowed });
          }}
        />
        {(tool.needs_credential || !core) && (
          <div className="ml-auto flex items-center gap-2">
            {tool.needs_credential && <ConfigurePopover tool={tool} />}
            {!core && (
              <Button
                variant="outline"
                size="sm"
                disabled={check.isPending}
                onClick={() => {
                  check.mutate();
                }}
              >
                {t("admin.aiTools.check")}
              </Button>
            )}
          </div>
        )}
      </div>
    </Card>
  );
}

function ConfigurePopover({ tool }: { tool: Tool }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const ensureId = ensureToolId(tool);
  const [credential, setCredential] = useState("");
  const savedProvider =
    typeof tool.config?.["provider"] === "string" ? tool.config["provider"] : "tavily";
  const [provider, setProvider] = useState(savedProvider);
  const savedCx = typeof tool.config?.["cx"] === "string" ? tool.config["cx"] : "";
  const [cx, setCx] = useState(savedCx);
  // Only web_search multiplexes engines; other credentialed tools keep config as is.
  const hasProviderChoice = tool.name === "web_search";
  // Google CSE is the one engine that needs a second identifier besides the key.
  const needsCx = hasProviderChoice && provider === "google_cse";

  const save = useMutation({
    mutationFn: async () =>
      patchTool(await ensureId(), {
        ...(hasProviderChoice
          ? { config: { ...tool.config, provider, ...(needsCx ? { cx } : {}) } }
          : {}),
        ...(credential ? { credential } : {}),
      }),
    onSuccess: () => {
      setCredential("");
      toast.success(t("admin.platform.saved"));
      void queryClient.invalidateQueries({ queryKey: aiKeys.tools });
    },
    onError: (error) => void toastApiError(error, t("admin.platform.saveFailed")),
  });

  const dirty =
    credential !== "" ||
    (hasProviderChoice && provider !== savedProvider) ||
    (needsCx && cx !== savedCx);

  return (
    <Popover>
      <PopoverTrigger render={<Button variant="outline" size="sm" />}>
        {t("admin.aiTools.configure")}
      </PopoverTrigger>
      <PopoverContent className="flex w-72 flex-col gap-2" align="start">
        {hasProviderChoice && (
          <>
            <Label>{t("admin.aiTools.provider")}</Label>
            <Select
              items={Object.entries(WEB_SEARCH_PROVIDERS).map(([value, label]) => ({
                value,
                label,
              }))}
              value={provider}
              onValueChange={(value) => {
                if (value) setProvider(value);
              }}
            >
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(WEB_SEARCH_PROVIDERS).map(([value, label]) => (
                  <SelectItem key={value} value={value}>
                    {label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-muted-foreground text-xs">{t("admin.aiTools.providerHint")}</p>
          </>
        )}
        {needsCx && (
          <>
            <Label htmlFor={`cx-${tool.name}`}>{t("admin.aiTools.cseCx")}</Label>
            <Input
              id={`cx-${tool.name}`}
              value={cx}
              onChange={(event) => {
                setCx(event.target.value);
              }}
            />
            <p className="text-muted-foreground text-xs">{t("admin.aiTools.cseCxHint")}</p>
          </>
        )}
        <Label htmlFor={`cred-${tool.name}`}>{t("admin.aiTools.credential")}</Label>
        <Input
          id={`cred-${tool.name}`}
          type="password"
          value={credential}
          placeholder={tool.credential_is_set ? "••••••••" : ""}
          onChange={(event) => {
            setCredential(event.target.value);
          }}
        />
        <p className="text-muted-foreground text-xs">{t("admin.aiTools.credentialHint")}</p>
        <Button
          size="sm"
          disabled={!dirty || save.isPending}
          onClick={() => {
            save.mutate();
          }}
        >
          {t("admin.platform.save")}
        </Button>
      </PopoverContent>
    </Popover>
  );
}
