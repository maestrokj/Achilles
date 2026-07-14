import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { InfoIcon, PencilLineIcon, ShieldCheckIcon, SparklesIcon } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "@/lib/toast";

import type { LucideIcon } from "lucide-react";

import { toastApiError } from "@/api/errors";
import { PageError } from "@/components/PageError";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";

import { aiKeys, getPrompt, patchPrompt } from "./api";
import type { PromptBlock } from "./types";

const PROMPT_MAX_CHARS = 6000;

/** Variables interpolated into both blocks at assembly time —
 * surfaced as chips so the admin sees what's available while editing. */
const PROMPT_VARIABLES = ["{org_name}", "{today}"] as const;

/** Admin · AI prompt: the two editable layers, read first and edited in place —
 * no modals, no preview. Wireframe: admin-panel/_wireframes/ai-behavior.html. */
export function AiBehaviorPage() {
  const { t } = useTranslation();
  const prompt = useQuery({ queryKey: aiKeys.prompt, queryFn: getPrompt });

  if (prompt.isError) return <PageError onRetry={() => void prompt.refetch()} />;

  return (
    <TooltipProvider delay={200}>
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-8">
        <PromptHeading />
        {prompt.isPending ? (
          <div className="flex flex-col gap-5">
            <Skeleton className="h-56 w-full rounded-xl" />
            <Skeleton className="h-56 w-full rounded-xl" />
          </div>
        ) : (
          <div className="flex flex-col gap-5">
            <PromptCard
              field="safety_text"
              icon={ShieldCheckIcon}
              title={t("admin.aiPrompt.safety")}
              hint={t("admin.aiPrompt.safetyHint")}
              block={prompt.data.safety}
            />
            <PromptCard
              field="org_text"
              icon={SparklesIcon}
              title={t("admin.aiPrompt.org")}
              hint={t("admin.aiPrompt.orgHint")}
              block={prompt.data.org}
            />
          </div>
        )}
      </div>
    </TooltipProvider>
  );
}

/** Title with the page intro tucked behind an info icon — kept off the
 * surface so the screen stays quiet, one hover away when needed. */
function PromptHeading() {
  const { t } = useTranslation();
  return (
    <header className="flex items-center gap-2">
      <h1 className="text-2xl font-semibold tracking-tight">{t("admin.nav.aiPrompt")}</h1>
      <Tooltip>
        <TooltipTrigger
          render={
            <button
              type="button"
              aria-label={t("common.moreInfo")}
              className="text-muted-foreground/50 hover:text-foreground inline-flex rounded-full p-1 transition-colors"
            />
          }
        >
          <InfoIcon className="size-4" />
        </TooltipTrigger>
        <TooltipContent className="max-w-sm leading-relaxed" align="start">
          {t("admin.aiPrompt.intro")}
        </TooltipContent>
      </Tooltip>
    </header>
  );
}

function PromptCard({
  field,
  icon: Icon,
  title,
  hint,
  block,
}: {
  field: "safety_text" | "org_text";
  icon: LucideIcon;
  title: string;
  hint: string;
  block: PromptBlock;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<string | null>(null);
  const editing = draft !== null;

  const save = useMutation({
    mutationFn: (text: string | null) => patchPrompt({ [field]: text }),
    onSuccess: (fresh) => {
      queryClient.setQueryData(aiKeys.prompt, fresh);
      setDraft(null);
      toast.success(t("admin.platform.saved"));
    },
    onError: (error) => void toastApiError(error, t("admin.platform.saveFailed")),
  });

  const value = draft ?? block.text;
  const over = value.length > PROMPT_MAX_CHARS;
  const unchanged = draft === block.text;

  return (
    <Card className="gap-0 py-0 transition-shadow hover:shadow-sm">
      <CardHeader className="flex flex-row items-center gap-3 px-5 py-4">
        <span className="bg-muted/70 text-muted-foreground grid size-9 shrink-0 place-items-center rounded-lg">
          <Icon className="size-[1.15rem]" strokeWidth={1.75} />
        </span>
        <div className="flex min-w-0 flex-col gap-0.5">
          <CardTitle className="flex items-center gap-1.5 text-sm">
            {title}
            <Tooltip>
              <TooltipTrigger
                render={
                  <button
                    type="button"
                    aria-label={title}
                    className="text-muted-foreground/60 hover:text-foreground -m-1 inline-flex rounded-full p-1 transition-colors"
                  />
                }
              >
                <InfoIcon className="size-3.5" />
              </TooltipTrigger>
              <TooltipContent className="max-w-xs leading-relaxed" align="start">
                {hint}
              </TooltipContent>
            </Tooltip>
          </CardTitle>
          <StateLabel isDefault={block.is_default} />
        </div>
      </CardHeader>

      <CardContent className="px-5 pb-4">
        {editing ? (
          <Textarea
            rows={8}
            value={value}
            aria-invalid={over}
            className="resize-y leading-relaxed"
            onChange={(event) => {
              setDraft(event.target.value);
            }}
          />
        ) : (
          <p className="text-foreground/90 max-h-64 overflow-y-auto text-sm leading-relaxed whitespace-pre-wrap">
            {block.text}
          </p>
        )}
      </CardContent>

      <CardFooter className="flex-wrap gap-x-4 gap-y-3 px-5 py-3">
        <VariableChips />
        {editing && <CharMeter used={value.length} over={over} />}
        <div className="ml-auto flex items-center gap-2">
          {editing ? (
            <>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setDraft(null);
                }}
              >
                {t("admin.platform.cancel")}
              </Button>
              <Button
                size="sm"
                disabled={over || unchanged || save.isPending}
                onClick={() => {
                  save.mutate(value);
                }}
              >
                {t("admin.platform.save")}
              </Button>
            </>
          ) : (
            <>
              {!block.is_default && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-muted-foreground"
                  disabled={save.isPending}
                  onClick={() => {
                    save.mutate(null);
                  }}
                >
                  {t("admin.aiPrompt.reset")}
                </Button>
              )}
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setDraft(block.text);
                }}
              >
                <PencilLineIcon className="size-3.5" />
                {t("admin.aiPrompt.edit")}
              </Button>
            </>
          )}
        </div>
      </CardFooter>
    </Card>
  );
}

/** Default vs overridden — a quiet dot + word, no loud badge. */
function StateLabel({ isDefault }: { isDefault: boolean }) {
  const { t } = useTranslation();
  return (
    <span className="text-muted-foreground flex items-center gap-1.5 text-xs">
      <span
        className={`size-1.5 rounded-full ${isDefault ? "bg-muted-foreground/40" : "bg-warning"}`}
      />
      {isDefault ? t("admin.aiPrompt.default") : t("admin.aiPrompt.customized")}
    </span>
  );
}

function VariableChips() {
  const { t } = useTranslation();
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-muted-foreground text-xs">{t("admin.aiPrompt.variables")}</span>
      {PROMPT_VARIABLES.map((name) => (
        <code
          key={name}
          className="bg-muted text-muted-foreground rounded px-1.5 py-0.5 font-mono text-[11px]"
        >
          {name}
        </code>
      ))}
    </div>
  );
}

/** Budget hairline: the shared window budget both blocks eat into —
 * calm while there's room, warns as the cap nears, alerts once over. */
function CharMeter({ used, over }: { used: number; over: boolean }) {
  const { t } = useTranslation();
  const ratio = Math.min(used / PROMPT_MAX_CHARS, 1);
  const near = !over && ratio > 0.9;
  const tone = over ? "text-destructive" : near ? "text-warning" : "text-muted-foreground";
  const fill = over ? "bg-destructive" : near ? "bg-warning" : "bg-muted-foreground/40";

  return (
    <div className="flex items-center gap-2">
      <span className="bg-muted h-1 w-14 overflow-hidden rounded-full">
        <span
          className={`block h-full rounded-full transition-all ${fill}`}
          style={{ width: `${String(Math.round(ratio * 100))}%` }}
        />
      </span>
      <span className={`text-xs tabular-nums ${tone}`}>
        {t("admin.aiPrompt.chars", { used, cap: PROMPT_MAX_CHARS })}
      </span>
    </div>
  );
}
