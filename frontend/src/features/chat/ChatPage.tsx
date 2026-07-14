/** /chat and /chat/:conversationId render this one component. A session (one
 * ChatThread mount) survives the lazy-creation replace-navigation /chat →
 * /chat/:id; any other navigation re-keys the thread. */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { HTTPError } from "ky";
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useLocation, useNavigate, useParams } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";

import { chatQueryKeys, getConversation } from "./api";
import { ChatThread } from "./Thread";
import type { Conversation } from "./types";

interface SessionIdentity {
  /** Remount key: bumping it gives the thread a fresh identity. */
  epoch: number;
  /** Conversation the session was mounted for (null = fresh chat). */
  mountId: number | null;
  /** Conversation the session created lazily mid-flight. */
  ownedId: number | null;
}

function parseConversationId(raw: string | undefined): number | null {
  if (!raw) return null;
  const parsed = Number(raw);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}

export function ChatPage() {
  const params = useParams<{ conversationId?: string }>();
  const urlId = parseConversationId(params.conversationId);
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [session, setSession] = useState<SessionIdentity>(() => ({
    epoch: 0,
    mountId: urlId,
    ownedId: null,
  }));
  const lastKeyRef = useRef(location.key);

  useEffect(() => {
    const keyChanged = location.key !== lastKeyRef.current;
    lastKeyRef.current = location.key;

    setSession((current) => {
      if (urlId !== null && urlId === current.mountId) return current;
      if (urlId !== null && urlId === current.ownedId) {
        // Our own replace-navigation after lazy creation — adopt, don't remount.
        return { ...current, mountId: urlId };
      }
      // Re-navigating to /chat (new chat) or landing on a foreign id → fresh session.
      if (urlId === null && current.mountId === null && !keyChanged) return current;
      return { epoch: current.epoch + 1, mountId: urlId, ownedId: null };
    });
  }, [urlId, location.key]);

  const handleCreated = useCallback(
    (id: number) => {
      setSession((current) => ({ ...current, ownedId: id }));
      // The lazily created dialogue must appear in the sidebar history.
      void queryClient.invalidateQueries({ queryKey: chatQueryKeys.conversations });
      void navigate(`/chat/${String(id)}`, { replace: true });
    },
    [navigate, queryClient],
  );

  return (
    <div className="h-full">
      <ChatSession
        key={session.epoch}
        conversationId={session.mountId}
        onConversationCreated={handleCreated}
      />
    </div>
  );
}

function ChatSession({
  conversationId,
  onConversationCreated,
}: {
  conversationId: number | null;
  onConversationCreated: (id: number) => void;
}) {
  // Identity is fixed at mount: when ChatPage later adopts the lazily created
  // id into mountId, the live thread must not be swapped for a history reload.
  const [mountConversationId] = useState(conversationId);

  if (mountConversationId === null) {
    return <ChatThread conversation={null} onConversationCreated={onConversationCreated} />;
  }
  return (
    <LoadedConversation id={mountConversationId} onConversationCreated={onConversationCreated} />
  );
}

const HISTORY_RETRIES = 2;

const DANGLING_POLL_MS = 1_500;
/** ~60s ceiling: covers a turn still finishing in the background, then stops
 * pestering the server for one that truly dropped (e.g. the tab was closed). */
const DANGLING_POLL_LIMIT = 40;

/** A tail that is a lone user turn means the reply has not landed yet — the turn
 * is either still finishing server-side (the adapter keeps it alive across a
 * navigation) or was cut before it began. Either way, keep watching for it. */
function awaitsReply(data: Conversation): boolean {
  return data.messages.at(-1)?.role === "user";
}

function LoadedConversation({
  id,
  onConversationCreated,
}: {
  id: number;
  onConversationCreated: (id: number) => void;
}) {
  const { t } = useTranslation();
  const pollsRef = useRef(0);
  const query = useQuery({
    queryKey: chatQueryKeys.conversation(id),
    queryFn: () => getConversation(id),
    // One fetch per session mount — the runtime owns the messages afterwards —
    // unless the tail is a reply still in flight, which refetchInterval polls.
    staleTime: Infinity,
    gcTime: 0,
    retry: (failureCount, error) =>
      failureCount < HISTORY_RETRIES &&
      !(error instanceof HTTPError && error.response.status === 404),
    refetchInterval: (q) => {
      const data = q.state.data;
      if (!data || !awaitsReply(data)) {
        pollsRef.current = 0;
        return false;
      }
      if (pollsRef.current >= DANGLING_POLL_LIMIT) return false;
      pollsRef.current += 1;
      return DANGLING_POLL_MS;
    },
  });

  if (query.isPending) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3">
        <Spinner />
        <p className="text-muted-foreground text-sm">{t("common.loading")}</p>
      </div>
    );
  }

  if (query.isError) {
    const notFound = query.error instanceof HTTPError && query.error.response.status === 404;
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3">
        <p className="text-muted-foreground text-sm">
          {notFound ? t("chat.history.notFound") : t("chat.history.loadError")}
        </p>
        {!notFound && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              void query.refetch();
            }}
          >
            {t("chat.history.retry")}
          </Button>
        )}
      </div>
    );
  }

  // Re-key when the awaited reply lands so the thread remounts around the now
  // complete history instead of freezing on the reply-less question.
  return (
    <ChatThread
      key={awaitsReply(query.data) ? "awaiting" : "settled"}
      conversation={query.data}
      onConversationCreated={onConversationCreated}
    />
  );
}
