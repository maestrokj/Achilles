/** The bell counter: live frames arrive over the events stream
 * (features/live/useEventStream); this background poll is the safety net that
 * keeps the counter honest while the stream is down. */

import { useQuery } from "@tanstack/react-query";

import { notificationKeys, unreadCount } from "./api";

const FALLBACK_POLL_MS = 120_000;

export function useUnreadCount() {
  return useQuery({
    queryKey: notificationKeys.unread,
    queryFn: unreadCount,
    refetchInterval: FALLBACK_POLL_MS,
    refetchOnWindowFocus: true,
  });
}
