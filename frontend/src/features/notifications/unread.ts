import type { NotificationItem } from "./types";

/** The one definition of "unread", shared by the bell, the feed and the preview.
 * A notification is unread until read — and a deduped series that re-fires *after*
 * being read resurfaces as unread (its last_seen_at outruns read_at), mirroring
 * the backend's unread_clause. */
export function isUnread(item: Pick<NotificationItem, "read_at" | "last_seen_at">): boolean {
  if (item.read_at === null) return true;
  return item.last_seen_at !== null && new Date(item.read_at) < new Date(item.last_seen_at);
}
