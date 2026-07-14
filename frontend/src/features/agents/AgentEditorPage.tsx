import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckIcon,
  DatabaseIcon,
  HistoryIcon,
  LockIcon,
  NetworkIcon,
  PlayIcon,
  PlusIcon,
  SigmaIcon,
  Trash2Icon,
  type LucideIcon,
} from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams } from "react-router-dom";

import { apiErrorReason } from "@/api/errors";
import { LIVE_STALE_TIME } from "@/api/freshness";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { BackLink } from "@/components/BackLink";
import { InfoHint } from "@/components/InfoHint";
import { TruncatedText } from "@/components/TruncatedText";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
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
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

import {
  agentsQueryKeys,
  createAgent,
  deleteAgent,
  getAgent,
  getAgentOptions,
  listRuns,
  patchAgent,
  runAgent,
} from "./api";
import { Banner } from "@/components/Banner";
import { RunJournal } from "./RunJournal";
import { ScheduleEditor } from "./ScheduleEditor";
import { StatusChip } from "./StatusChip";
import type { Agent, AgentOptions, ScheduleSpec } from "./types";

/** Icons per core tool name; unknown names fall back to the database icon. */
const CORE_TOOL_ICONS: Partial<Record<string, LucideIcon>> = {
  search: DatabaseIcon,
  graph: NetworkIcon,
  sql: SigmaIcon,
};

/** Web App · Agent editor with the run journal.
 * Wireframe: web-app/_wireframes/agent-editor.html. */
export function AgentEditorPage() {
  const { agentId } = useParams();
  const isNew = agentId === undefined;
  const id = isNew ? null : Number(agentId);
  const { t } = useTranslation();

  const agentQuery = useQuery({
    queryKey: agentsQueryKeys.agent(id ?? 0),
    queryFn: () => getAgent(id ?? 0),
    enabled: id !== null,
    staleTime: LIVE_STALE_TIME,
  });
  const optionsQuery = useQuery({
    queryKey: agentsQueryKeys.options,
    queryFn: getAgentOptions,
  });

  if (!isNew && agentQuery.isPending)
    return (
      <div className="h-full overflow-y-auto">
        <PageSkeleton className="px-6 py-8" />
      </div>
    );
  if (!isNew && agentQuery.isError) {
    return (
      <PageError
        className="px-6 py-8"
        description={t("agents.errors.loadFailed")}
        onRetry={() => void agentQuery.refetch()}
      />
    );
  }

  const agent = agentQuery.data ?? null;
  return (
    <EditorForm
      // Remount on identity change so the form state re-initializes from data.
      key={agent ? String(agent.id) : "new"}
      agent={agent}
      options={optionsQuery.data ?? null}
    />
  );
}

interface FormState {
  name: string;
  description: string;
  prompt: string;
  schedule: ScheduleSpec | null;
  model_id: number | null;
  tool_ids: number[];
}

/** A form section: card with a heading over a divider, then the fields. */
function FormSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card className="shadow-2xs">
      <CardContent className="flex flex-col gap-4">
        <h2 className="-mx-(--card-spacing) border-b px-(--card-spacing) pb-3 text-sm font-semibold">
          {title}
        </h2>
        {children}
      </CardContent>
    </Card>
  );
}

function EditorForm({ agent, options }: { agent: Agent | null; options: AgentOptions | null }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const id = agent?.id ?? null;

  const [form, setForm] = useState<FormState>(() =>
    agent
      ? {
          name: agent.name,
          description: agent.description ?? "",
          prompt: agent.prompt,
          schedule: agent.schedule,
          model_id: agent.model_id,
          tool_ids: agent.tool_ids,
        }
      : { name: "", description: "", prompt: "", schedule: null, model_id: null, tool_ids: [] },
  );
  const [error, setError] = useState<string | null>(null);

  // Targeted: the options catalog and the admin queries live untouched.
  const invalidate = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: agentsQueryKeys.list }),
      ...(id === null
        ? []
        : [
            queryClient.invalidateQueries({ queryKey: agentsQueryKeys.agent(id) }),
            queryClient.invalidateQueries({ queryKey: agentsQueryKeys.runs(id) }),
          ]),
    ]);
  };

  const failWith = async (cause: unknown) => {
    setError(await apiErrorReason(cause));
  };

  const save = useMutation({
    mutationFn: () => {
      const body = {
        name: form.name,
        description: form.description || null,
        prompt: form.prompt,
        schedule: form.schedule,
        model_id: form.model_id,
        tool_ids: form.tool_ids,
      };
      return id === null ? createAgent(body) : patchAgent(id, body);
    },
    onSuccess: async (saved: Agent) => {
      setError(null);
      await invalidate();
      if (id === null) await navigate(`/agents/${String(saved.id)}`);
    },
    onError: failWith,
  });

  const toggleEnabled = useMutation({
    mutationFn: (enabled: boolean) => patchAgent(id ?? 0, { enabled }),
    onSettled: invalidate,
  });

  const runNow = useMutation({
    mutationFn: () => runAgent(id ?? 0),
    onSuccess: async () => {
      setError(null);
      await invalidate();
    },
    onError: failWith,
  });

  const remove = useMutation({
    mutationFn: () => deleteAgent(id ?? 0),
    onSuccess: async () => {
      await invalidate();
      await navigate("/agents");
    },
    onError: failWith,
  });

  const modelGone = agent !== null && agent.status === "model_missing";
  const overBudget = agent?.status === "budget_exceeded";
  const paused = agent?.admin_paused ?? false;
  const statusHint = paused
    ? t("agents.editor.adminPausedBanner")
    : modelGone
      ? t("agents.editor.modelMissingBanner")
      : overBudget
        ? t("agents.editor.budgetBanner")
        : null;
  // A spent budget is a soft, self-clearing stop (warning); the durable ones
  // (admin pause, missing model) read as destructive.
  const statusHintClass = cn(
    "inline-flex align-middle transition-colors",
    overBudget
      ? "text-warning hover:text-warning/80"
      : "text-destructive hover:text-destructive/80",
  );

  const modelOptions = (options?.models ?? []).map((model) => ({
    value: String(model.id),
    label: model.display_name,
  }));

  return (
    <div className="h-full overflow-y-auto">
      <div className="animate-in fade-in slide-in-from-bottom-1 mx-auto flex max-w-3xl flex-col gap-6 px-6 py-8 duration-500">
        <BackLink to="/agents" label={t("agents.title")} />
        <div className="flex items-start gap-16">
          <div className="flex min-w-0 flex-1 flex-col gap-2">
            <h1 className="text-2xl font-semibold tracking-tight">
              <TruncatedText>
                {agent === null ? t("agents.editor.newTitle") : agent.name}
              </TruncatedText>
            </h1>
            {agent && (
              <div className="flex items-center gap-1.5">
                <StatusChip status={agent.status} />
                {statusHint && <InfoHint text={statusHint} className={statusHintClass} />}
              </div>
            )}
          </div>
          {agent && (
            <div className="flex flex-col items-end gap-2">
              <div className="flex items-center gap-3">
                <Switch
                  aria-label={t("agents.editor.enabled")}
                  checked={agent.enabled}
                  disabled={paused}
                  onCheckedChange={(enabled) => {
                    toggleEnabled.mutate(enabled);
                  }}
                />
                <Button
                  variant="outline"
                  className="border-success/40 bg-success/10 text-success hover:bg-success/20 hover:text-success dark:bg-success/20 dark:hover:bg-success/30 w-48"
                  disabled={agent.status !== "active" || runNow.isPending}
                  onClick={() => {
                    runNow.mutate();
                  }}
                >
                  <PlayIcon data-icon="inline-start" />
                  {t("agents.editor.runNow")}
                </Button>
              </div>
              <AlertDialog>
                <AlertDialogTrigger
                  render={
                    <Button
                      variant="outline"
                      className="text-destructive border-destructive/30 hover:bg-destructive/10 hover:text-destructive w-48"
                    />
                  }
                >
                  <Trash2Icon data-icon="inline-start" />
                  {t("agents.editor.delete")}
                </AlertDialogTrigger>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>{t("agents.editor.deleteConfirmTitle")}</AlertDialogTitle>
                    <AlertDialogDescription>
                      {t("agents.editor.deleteConfirmBody")}
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>{t("agents.editor.cancel")}</AlertDialogCancel>
                    <AlertDialogAction
                      variant="destructive"
                      onClick={() => {
                        remove.mutate();
                      }}
                    >
                      {t("agents.editor.deleteConfirmAction")}
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            </div>
          )}
        </div>

        {error && <Banner tone="destructive">{error}</Banner>}

        <FormSection title={t("agents.editor.sections.basics.title")}>
          <div className="flex flex-col gap-2">
            <Label htmlFor="agent-name">{t("agents.editor.name")}</Label>
            <Input
              id="agent-name"
              value={form.name}
              placeholder={t("agents.editor.namePlaceholder")}
              onChange={(event) => {
                setForm({ ...form, name: event.target.value });
              }}
            />
          </div>

          <div className="flex flex-col gap-2">
            <Label htmlFor="agent-description">{t("agents.editor.description")}</Label>
            <Textarea
              id="agent-description"
              value={form.description}
              placeholder={t("agents.editor.descriptionPlaceholder")}
              rows={3}
              onChange={(event) => {
                setForm({ ...form, description: event.target.value });
              }}
            />
          </div>

          <div className="flex flex-col gap-2">
            <Label htmlFor="agent-prompt">{t("agents.editor.prompt")}</Label>
            <Textarea
              id="agent-prompt"
              value={form.prompt}
              placeholder={t("agents.editor.promptPlaceholder")}
              rows={6}
              onChange={(event) => {
                setForm({ ...form, prompt: event.target.value });
              }}
            />
          </div>
        </FormSection>

        <FormSection title={t("agents.editor.sections.tools.title")}>
          <div className="flex flex-col gap-2">
            <div className="flex items-center gap-1.5">
              <Label>{t("agents.editor.model")}</Label>
              <InfoHint
                text={t("agents.editor.modelHint")}
                className="text-foreground hover:text-foreground/70 inline-flex align-middle transition-colors"
              />
            </div>
            <Select
              items={modelOptions}
              value={form.model_id === null ? null : String(form.model_id)}
              onValueChange={(next) => {
                setForm({ ...form, model_id: next === null ? null : Number(next) });
              }}
            >
              <SelectTrigger className="w-64">
                <SelectValue placeholder={t("agents.editor.modelNone")} />
              </SelectTrigger>
              <SelectContent>
                {modelOptions.length === 0 && (
                  <p className="text-muted-foreground px-2.5 py-2 text-sm">
                    {t("agents.editor.noModels")}
                  </p>
                )}
                {modelOptions.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="flex flex-col gap-2 border-t pt-4">
            <Label>{t("agents.editor.internalTools")}</Label>
            <div className="flex flex-wrap gap-2">
              {(options?.core_tools ?? []).map((name) => {
                const Icon = CORE_TOOL_ICONS[name] ?? DatabaseIcon;
                return (
                  <span
                    key={name}
                    className="bg-secondary text-secondary-foreground inline-flex items-center gap-1.5 rounded-full border border-transparent px-3 py-0.5 text-sm"
                  >
                    <LockIcon className="size-3.5 opacity-70" />
                    <Icon className="size-3.5" />
                    {name}
                  </span>
                );
              })}
            </div>
          </div>

          <div className="flex flex-col gap-2 border-t pt-4">
            <Label>{t("agents.editor.externalTools")}</Label>
            <div className="flex flex-wrap items-center gap-2">
              {(options?.tools ?? []).map((tool) => {
                const picked = form.tool_ids.includes(tool.id);
                return (
                  <button
                    key={tool.id}
                    type="button"
                    onClick={() => {
                      setForm({
                        ...form,
                        tool_ids: picked
                          ? form.tool_ids.filter((toolId) => toolId !== tool.id)
                          : [...form.tool_ids, tool.id],
                      });
                    }}
                    className={cn(
                      "inline-flex items-center gap-1.5 rounded-full border px-3 py-0.5 text-sm transition-colors",
                      picked
                        ? "bg-secondary text-secondary-foreground border-transparent"
                        : "border-border text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                    )}
                  >
                    {picked ? (
                      <CheckIcon className="size-3.5" />
                    ) : (
                      <PlusIcon className="size-3.5 opacity-60" />
                    )}
                    {tool.name}
                  </button>
                );
              })}
              {(agent?.disabled_tools ?? []).map((tool) => (
                <span
                  key={tool.id}
                  className="border-border bg-muted/40 text-muted-foreground inline-flex items-center gap-1.5 rounded-full border border-dashed px-3 py-0.5 text-sm"
                >
                  <LockIcon className="size-3.5 opacity-70" />
                  <span>{tool.name}</span>
                  <InfoHint
                    text={t("agents.editor.toolDisabledByAdmin")}
                    className="text-muted-foreground hover:text-foreground inline-flex align-middle transition-colors"
                  />
                </span>
              ))}
              <span className="border-border bg-background/60 text-muted-foreground inline-flex shrink-0 items-center gap-1.5 rounded-full border px-3 py-0.5 text-sm font-medium">
                <span className="bg-muted-foreground/50 size-1.5 rounded-full" />
                {t("agents.editor.mcpSoon")}
              </span>
            </div>
          </div>
        </FormSection>

        <FormSection title={t("agents.editor.sections.schedule.title")}>
          <ScheduleEditor
            value={form.schedule}
            onChange={(schedule) => {
              setForm({ ...form, schedule });
            }}
          />
        </FormSection>

        <div className="flex items-center justify-end gap-2">
          <Button
            variant="ghost"
            onClick={() => {
              void navigate("/agents");
            }}
          >
            {t("agents.editor.cancel")}
          </Button>
          <Button
            disabled={!form.name.trim() || !form.prompt.trim() || save.isPending}
            onClick={() => {
              save.mutate();
            }}
          >
            {t("agents.editor.save")}
          </Button>
        </div>

        {id !== null && (
          <section className="mt-2 flex flex-col gap-3">
            <div className="flex items-center gap-2">
              <HistoryIcon className="text-muted-foreground size-4" aria-hidden="true" />
              <h2 className="text-sm font-semibold">{t("agents.journal.title")}</h2>
            </div>
            <RunJournal
              queryKey={agentsQueryKeys.runs(id)}
              fetchPage={(cursor) => listRuns(id, cursor)}
            />
          </section>
        )}
      </div>
    </div>
  );
}
