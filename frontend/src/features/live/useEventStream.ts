/** The one push connection per tab: `/events/stream` → query invalidation.
 *
 * `board` frames invalidate that board's keys (the registry); `unread` frames
 * feed the bell counter; `hello` (sent on every connect) invalidates all
 * subscribed boards, so a reconnect catches up on everything missed while the
 * wire was down. Reconnect/backoff/watchdog live in lib/sse. Mounted once per
 * shell, next to the bell. */

import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { notificationKeys } from "@/features/notifications/api";
import { streamWithReconnect, type SseEvent } from "@/lib/sse";

import { isBoard, LIVE_BOARDS } from "./registry";

function invalidateBoard(queryClient: QueryClient, board: unknown): void {
  if (!isBoard(board)) return;
  for (const queryKey of LIVE_BOARDS[board]) {
    void queryClient.invalidateQueries({ queryKey });
  }
}

function handleEvent(queryClient: QueryClient, event: SseEvent): void {
  const data = event.data as Record<string, unknown> | null;
  switch (event.name) {
    case "hello": {
      const boards = Array.isArray(data?.boards) ? data.boards : [];
      for (const board of boards) invalidateBoard(queryClient, board);
      break;
    }
    case "board": {
      invalidateBoard(queryClient, data?.board);
      break;
    }
    case "unread": {
      queryClient.setQueryData(notificationKeys.unread, data);
      void queryClient.invalidateQueries({ queryKey: ["notifications", "feed"] });
      break;
    }
    // "ping" only feeds the watchdog inside streamWithReconnect
  }
}

export function useEventStream(): void {
  const queryClient = useQueryClient();

  useEffect(() => {
    const controller = new AbortController();
    void streamWithReconnect("events/stream", controller.signal, (event) => {
      handleEvent(queryClient, event);
    });
    return () => {
      controller.abort();
    };
  }, [queryClient]);
}
