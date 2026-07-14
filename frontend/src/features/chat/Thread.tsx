/** The assistant-ui thread skinned with our shadcn tokens. An empty thread is
 * a centered hero (serif greeting + composer); a running dialogue is the
 * classic viewport + bottom composer. One mount = one conversation identity;
 * ChatPage owns remounts. */

import {
  AssistantRuntimeProvider,
  AuiIf,
  ComposerPrimitive,
  ErrorPrimitive,
  MessagePartPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useAuiState,
  useLocalRuntime,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import { MarkdownTextPrimitive } from "@assistant-ui/react-markdown";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowUpIcon, SquareIcon } from "lucide-react";
import { useCallback, useMemo, useState, type ComponentProps } from "react";
import { useTranslation } from "react-i18next";
import remarkGfm from "remark-gfm";

import { Banner } from "@/components/Banner";
import { Spinner } from "@/components/ui/spinner";
import { useSession } from "@/features/auth/session-context";
import { cn } from "@/lib/utils";

import { chatErrorMessage, createChatAdapter } from "./adapter";
import { chatQueryKeys } from "./api";
import { CitationMark } from "./CitationMark";
import { Feedback } from "./Feedback";
import { GroundingState } from "./GroundingState";
import { MessageSources } from "./MessageSources";
import { ModelPicker } from "./ModelPicker";
import { remarkCiteMarkers } from "./remark-cite-markers";
import type { ChatModelsResponse, Conversation, MessageOverlay } from "./types";

function toThreadMessages(conversation: Conversation): ThreadMessageLike[] {
  return conversation.messages.map((message) => {
    const overlay: MessageOverlay = {
      assistantMessageId: message.role === "assistant" ? message.id : null,
      conversationId: conversation.id,
      grounding: null, // the replay carries citations only — no plaque on old turns
      citations: message.citations ?? [],
      feedback: message.feedback,
      searching: null,
      finish: message.finish,
      errorCode: message.error_code,
    };
    return {
      id: String(message.id),
      role: message.role,
      content: [{ type: "text" as const, text: message.content }],
      createdAt: new Date(message.created_at),
      metadata: message.role === "assistant" ? { custom: { overlay } } : undefined,
    };
  });
}

function readOverlay(custom: Record<string, unknown> | undefined): MessageOverlay | undefined {
  return (custom as { overlay?: MessageOverlay } | undefined)?.overlay;
}

function MarkdownLink(props: ComponentProps<"a">) {
  return (
    <a
      href={props.href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-primary underline underline-offset-2"
    >
      {props.children}
    </a>
  );
}

/** GFM tables can outgrow the bubble — let a wide one scroll on its own axis
 * instead of stretching the message column. */
function MarkdownTable(props: ComponentProps<"table">) {
  return (
    <div className="overflow-x-auto">
      <table {...props} />
    </div>
  );
}

/** External images never load — an image degrades to a plain outbound link. */
function MarkdownImage(props: ComponentProps<"img">) {
  const url = typeof props.src === "string" ? props.src : null;
  if (!url) return null;
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className="text-primary underline underline-offset-2"
    >
      {props.alt || url}
    </a>
  );
}

function AssistantText() {
  return (
    <MarkdownTextPrimitive
      remarkPlugins={[remarkGfm, remarkCiteMarkers]}
      className="[&_code]:bg-muted [&_pre]:bg-muted [&_blockquote]:border-border [&_blockquote]:text-muted-foreground [&_td]:border-border [&_th]:border-border [&_th]:bg-muted/50 space-y-2.5 text-[15px] leading-7 break-words [&_blockquote]:border-l-2 [&_blockquote]:pl-3 [&_blockquote]:italic [&_code]:rounded [&_code]:px-1 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-[13px] [&_h1]:text-lg [&_h1]:font-semibold [&_h2]:text-base [&_h2]:font-semibold [&_h3]:text-[15px] [&_h3]:font-semibold [&_ol]:list-decimal [&_ol]:pl-5 [&_pre]:overflow-x-auto [&_pre]:rounded-xl [&_pre]:p-3.5 [&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_table]:my-1 [&_table]:w-full [&_table]:border-collapse [&_table]:text-[13px] [&_td]:border [&_td]:px-2.5 [&_td]:py-1.5 [&_th]:border [&_th]:px-2.5 [&_th]:py-1.5 [&_th]:text-left [&_th]:font-semibold [&_ul]:list-disc [&_ul]:pl-5"
      components={{ a: MarkdownLink, img: MarkdownImage, table: MarkdownTable, cite: CitationMark }}
    />
  );
}

function UserMessage() {
  return (
    <MessagePrimitive.Root className="flex justify-end">
      <div className="bg-accent text-accent-foreground max-w-[80%] rounded-2xl rounded-br-md px-4 py-2.5 text-[15px] leading-6 break-words whitespace-pre-wrap">
        <MessagePrimitive.Parts components={{ Text: () => <MessagePartPrimitive.Text /> }} />
      </div>
    </MessagePrimitive.Root>
  );
}

function SearchingIndicator() {
  const { t } = useTranslation();
  return (
    <div className="text-muted-foreground flex items-center gap-2 text-xs">
      <Spinner className="size-3" />
      {t("chat.searching")}
    </div>
  );
}

/** Calm placeholder while the answer is still forming and no text has arrived —
 * three warm dots breathing in sequence, so a silent turn never reads as a hang. */
function ThinkingIndicator() {
  const { t } = useTranslation();
  return (
    <div className="flex items-center gap-1 py-1" role="status" aria-label={t("chat.thinking")}>
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="bg-muted-foreground/50 size-1.5 animate-pulse rounded-full"
          style={{ animationDelay: `${String(i * 180)}ms`, animationDuration: "1s" }}
        />
      ))}
    </div>
  );
}

/** The live failure plaque (ErrorPrimitive) redrawn for a replayed failed turn —
 * on reload the reason rides in the persisted overlay, not the runtime, so the
 * notice looks identical before and after a refresh. */
function ReplayErrorNotice({ code }: { code: string | null }) {
  return (
    <Banner tone="destructive" compact>
      {chatErrorMessage(code ?? undefined)}
    </Banner>
  );
}

/** A calm one-liner under a turn the user stopped on purpose — not an error. */
function StoppedNote() {
  const { t } = useTranslation();
  return <p className="text-muted-foreground text-xs">{t("chat.turn.stopped")}</p>;
}

function AssistantMessage() {
  const overlay = readOverlay(useAuiState((state) => state.message.metadata.custom));
  const isRunning = useAuiState(
    (state) => state.message.role === "assistant" && state.message.status.type === "running",
  );
  // An error-only turn has no text — the bubble would render as an empty pill.
  const hasText = useAuiState((state) =>
    state.message.content.some((part) => part.type === "text" && part.text.trim().length > 0),
  );
  const settled = !isRunning;

  return (
    <MessagePrimitive.Root className="flex flex-col items-start gap-3">
      {hasText && (
        <div className="bg-card border-border/60 text-foreground w-fit max-w-full min-w-0 rounded-2xl rounded-bl-md border px-4 py-3 shadow-2xs">
          <MessagePrimitive.Parts components={{ Text: AssistantText }} />
          {settled && overlay && overlay.citations.length > 0 && (
            <MessageSources citations={overlay.citations} conversationId={overlay.conversationId} />
          )}
        </div>
      )}

      {isRunning && !hasText && !overlay?.searching && <ThinkingIndicator />}

      {/* Live failure: the runtime holds the thrown error. */}
      <MessagePrimitive.Error>
        <ErrorPrimitive.Root>
          <Banner tone="destructive" compact>
            <ErrorPrimitive.Message />
          </Banner>
        </ErrorPrimitive.Root>
      </MessagePrimitive.Error>

      {/* Replayed failure: the same plaque, driven by the persisted outcome. */}
      {settled && overlay?.finish === "failed" && <ReplayErrorNotice code={overlay.errorCode} />}
      {settled && overlay?.finish === "stopped" && hasText && <StoppedNote />}

      {overlay?.searching && isRunning && <SearchingIndicator />}
      {settled && overlay?.grounding && <GroundingState grounding={overlay.grounding} />}
      {settled && overlay?.finish !== "failed" && overlay?.assistantMessageId != null && (
        <Feedback messageId={overlay.assistantMessageId} initial={overlay.feedback} />
      )}
    </MessagePrimitive.Root>
  );
}

/** Serif greeting of the empty thread, keyed by the local hour. */
function heroKey(hour: number): "morning" | "day" | "evening" | "night" {
  if (hour >= 5 && hour < 12) return "morning";
  if (hour >= 12 && hour < 17) return "day";
  if (hour >= 17 && hour < 23) return "evening";
  return "night";
}

function Hero() {
  const { t } = useTranslation();
  const session = useSession();
  const fullName = session.status === "authenticated" ? session.user.full_name : "";
  const firstName = fullName.trim().split(/\s+/)[0] ?? "";

  return (
    <div className="animate-in fade-in-0 slide-in-from-bottom-2 px-5 text-center duration-500 [animation-fill-mode:backwards]">
      <h1 className="text-foreground/90 font-serif text-3xl tracking-tight text-balance md:text-4xl">
        {t(`chat.hero.${heroKey(new Date().getHours())}`, { name: firstName })}
      </h1>
      <p className="text-muted-foreground mt-3 text-[15px] text-balance">
        {t("chat.empty.subtitle")}
      </p>
    </div>
  );
}

/** Quiet starter prompts under the empty composer — a way in, not a menu.
 * Each chip submits its own text through the runtime via ThreadPrimitive.Suggestion. */
const STARTER_KEYS = ["a", "b", "c"] as const;

function Suggestions() {
  const { t } = useTranslation();

  return (
    <div className="animate-in fade-in-0 flex max-w-2xl flex-wrap justify-center gap-2 px-5 duration-500 [animation-delay:220ms] [animation-fill-mode:backwards]">
      {STARTER_KEYS.map((key) => {
        const prompt = t(`chat.suggestions.${key}`);
        return (
          <ThreadPrimitive.Suggestion
            key={key}
            prompt={prompt}
            send
            className="border-border/70 text-muted-foreground hover:border-border hover:bg-accent hover:text-foreground focus-visible:ring-ring rounded-full border px-3.5 py-2 text-sm transition-colors outline-none focus-visible:ring-2"
          >
            {prompt}
          </ThreadPrimitive.Suggestion>
        );
      })}
    </div>
  );
}

function Composer({
  selectedModel,
  onSelectModel,
  className,
}: {
  selectedModel: string | null;
  onSelectModel: (modelId: string) => void;
  className?: string;
}) {
  const { t } = useTranslation();

  return (
    <ComposerPrimitive.Root
      className={cn(
        "border-border bg-card flex w-full flex-col gap-1 rounded-3xl border p-3 shadow-sm",
        className,
      )}
    >
      <ComposerPrimitive.Input
        rows={1}
        placeholder={t("chat.composer.placeholder")}
        className="placeholder:text-muted-foreground max-h-48 min-h-11 w-full resize-none bg-transparent px-2 py-2 text-[15px] leading-6 outline-none"
      />
      <div className="flex items-center gap-1.5">
        <ModelPicker selected={selectedModel} onSelect={onSelectModel} />
        <span className="flex-1" />
        <AuiIf condition={(state) => !state.thread.isRunning}>
          <ComposerPrimitive.Send
            aria-label={t("chat.composer.send")}
            className="bg-primary text-primary-foreground hover:bg-primary/85 focus-visible:ring-ring flex size-9 items-center justify-center rounded-full transition-colors outline-none focus-visible:ring-2 disabled:opacity-40 [&_svg]:size-4.5"
          >
            <ArrowUpIcon />
          </ComposerPrimitive.Send>
        </AuiIf>
        <AuiIf condition={(state) => state.thread.isRunning}>
          <ComposerPrimitive.Cancel
            aria-label={t("chat.composer.stop")}
            className="bg-foreground text-background focus-visible:ring-ring flex size-9 items-center justify-center rounded-full transition-colors outline-none focus-visible:ring-2 [&_svg]:size-3.5"
          >
            <SquareIcon />
          </ComposerPrimitive.Cancel>
        </AuiIf>
      </div>
    </ComposerPrimitive.Root>
  );
}

/** One conversation's thread: the LocalRuntime lives here, so the component
 * must be remounted (keyed) to switch identity — ChatPage takes care of that. */
export function ChatThread({
  conversation,
  onConversationCreated,
}: {
  conversation: Conversation | null;
  onConversationCreated: (id: number) => void;
}) {
  const queryClient = useQueryClient();
  const [selectedModel, setSelectedModel] = useState<string | null>(
    conversation?.selected_model ?? null,
  );

  const initialConversationId = conversation?.id ?? null;
  const { adapter, setModel } = useMemo(
    () => createChatAdapter({ initialConversationId, onConversationCreated }),
    [initialConversationId, onConversationCreated],
  );

  // Captured once: the runtime owns the messages after mount.
  const [initialMessages] = useState<ThreadMessageLike[]>(() =>
    conversation ? toThreadMessages(conversation) : [],
  );
  const runtime = useLocalRuntime(adapter, { initialMessages });

  const handleSelectModel = useCallback(
    (modelId: string) => {
      setModel(modelId);
      setSelectedModel(modelId);
      // The turn this pick rides on will persist it as the user's personal
      // default server-side; reflect that in the cached picker now, so a fresh
      // composer opens on this model. Without it the /chat/models cache keeps its
      // stale `selected` for the whole 5-min staleTime — a new chat would fall
      // back to the admin default until a hard reload refetched it.
      queryClient.setQueryData<ChatModelsResponse>(chatQueryKeys.models, (old) =>
        old ? { ...old, selected: modelId } : old,
      );
    },
    [setModel, queryClient],
  );

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ThreadPrimitive.Root className="flex h-full flex-col">
        {/* Empty thread — the hero: greeting + composer, vertically centered. */}
        <AuiIf condition={(state) => state.thread.isEmpty}>
          <div className="flex h-full flex-col items-center justify-center gap-7 pb-16">
            <Hero />
            <div className="animate-in fade-in-0 slide-in-from-bottom-2 w-full max-w-2xl px-5 duration-500 [animation-delay:120ms] [animation-fill-mode:backwards]">
              <Composer selectedModel={selectedModel} onSelectModel={handleSelectModel} />
            </div>
            <Suggestions />
          </div>
        </AuiIf>

        {/* Live dialogue — the composer floats over the viewport, so a soft
            gradient fades the scrolling messages out beneath it. */}
        <AuiIf condition={(state) => !state.thread.isEmpty}>
          <div className="relative flex min-h-0 flex-1 flex-col">
            <ThreadPrimitive.Viewport autoScroll className="min-h-0 flex-1 overflow-y-auto px-5">
              <div className="mx-auto flex w-full max-w-3xl flex-col gap-8 pt-8 pb-40">
                <ThreadPrimitive.Messages>
                  {({ message }) =>
                    message.role === "user" ? <UserMessage /> : <AssistantMessage />
                  }
                </ThreadPrimitive.Messages>
              </div>
            </ThreadPrimitive.Viewport>
            <div className="from-background via-background pointer-events-none absolute inset-x-0 bottom-0 bg-gradient-to-t to-transparent px-5 pt-14 pb-5">
              <div className="pointer-events-auto mx-auto w-full max-w-3xl">
                <Composer selectedModel={selectedModel} onSelectModel={handleSelectModel} />
              </div>
            </div>
          </div>
        </AuiIf>
      </ThreadPrimitive.Root>
    </AssistantRuntimeProvider>
  );
}
