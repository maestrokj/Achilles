/** Calls to /notifications (feed · prefs) and /admin/notification-* (config). */

import { api } from "@/api/client";
import { qs, type ListQuery, type OffsetPage } from "@/api/lists";

import type { Channel, NotificationItem, Pref, RouteCell, WebhookPreset } from "./types";

export const notificationKeys = {
  unread: ["notifications", "unread"] as const,
  feed: (query: ListQuery) => ["notifications", "feed", query] as const,
  prefs: ["notifications", "prefs"] as const,
  channels: ["admin", "notification-channels"] as const,
  routes: ["admin", "notification-routes"] as const,
};

export function listNotifications(query: ListQuery): Promise<OffsetPage<NotificationItem>> {
  return api.get("notifications", { searchParams: qs(query) }).json<OffsetPage<NotificationItem>>();
}

export function unreadCount(): Promise<{ count: number }> {
  return api.get("notifications/unread").json<{ count: number }>();
}

export async function markRead(id: number): Promise<void> {
  await api.post(`notifications/${String(id)}/read`);
}

export async function markAllRead(): Promise<void> {
  await api.post("notifications/read-all");
}

export function getPrefs(): Promise<{ items: Pref[] }> {
  return api.get("notifications/prefs").json<{ items: Pref[] }>();
}

export function putPrefs(items: Pref[]): Promise<{ items: Pref[] }> {
  return api.put("notifications/prefs", { json: { items } }).json<{ items: Pref[] }>();
}

// --- Admin config ---

export function listChannels(): Promise<{ items: Channel[] }> {
  return api.get("admin/notification-channels").json<{ items: Channel[] }>();
}

export function createWebhook(body: {
  name: string;
  preset: WebhookPreset;
  url: string;
  secret?: string;
}): Promise<Channel> {
  return api.post("admin/notification-channels", { json: body }).json<Channel>();
}

export function patchChannel(
  id: number,
  body: { name?: string; url?: string; secret?: string; enabled?: boolean },
): Promise<Channel> {
  return api.patch(`admin/notification-channels/${String(id)}`, { json: body }).json<Channel>();
}

export async function deleteChannel(id: number): Promise<void> {
  await api.delete(`admin/notification-channels/${String(id)}`);
}

export function testChannel(id: number): Promise<{ ok: boolean; error: string | null }> {
  return api
    .post(`admin/notification-channels/${String(id)}/test`)
    .json<{ ok: boolean; error: string | null }>();
}

export function listRoutes(): Promise<{ items: RouteCell[] }> {
  return api.get("admin/notification-routes").json<{ items: RouteCell[] }>();
}

export function patchRoutes(
  items: { event_type: string; channel_id: number; enabled: boolean }[],
): Promise<{ items: RouteCell[] }> {
  return api.patch("admin/notification-routes", { json: { items } }).json<{ items: RouteCell[] }>();
}
