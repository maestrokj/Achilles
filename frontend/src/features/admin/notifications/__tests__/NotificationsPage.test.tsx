import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { toast } from "@/lib/toast";

import type { SmtpSettings } from "@/features/admin/platform/types";
import type { Channel, RouteCell } from "@/features/notifications/types";
import { apiUrl, server } from "@/test/msw";
import { renderAs } from "@/test/session";

import { NotificationsPage } from "../NotificationsPage";

vi.mock("@/lib/toast", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function smtp(available: boolean): SmtpSettings {
  return {
    is_enabled: available,
    host: available ? "smtp.acme.example" : null,
    port: available ? 587 : null,
    security: "starttls",
    username: null,
    password_mask: null,
    from_address: available ? "bot@acme.example" : null,
    is_available: available,
    last_test_ok: null,
    last_test_at: null,
  };
}

const CHANNELS: Channel[] = [
  {
    id: 1,
    kind: "in_app",
    preset: null,
    name: "In-app",
    is_builtin: true,
    enabled: true,
    url_mask: null,
    secret_set: false,
    last_test_ok: null,
    last_test_at: null,
  },
  {
    id: 2,
    kind: "email",
    preset: null,
    name: "Email",
    is_builtin: true,
    enabled: true,
    url_mask: null,
    secret_set: false,
    last_test_ok: null,
    last_test_at: null,
  },
  {
    id: 3,
    kind: "webhook",
    preset: "slack",
    name: "Ops",
    is_builtin: false,
    enabled: true,
    url_mask: "••••hook",
    secret_set: false,
    last_test_ok: true,
    last_test_at: "2026-07-04T10:00:00Z",
  },
];

const ROUTES: RouteCell[] = [
  { event_type: "security", channel_id: 1, enabled: true, locked: true, severity: "critical" },
  { event_type: "security", channel_id: 2, enabled: true, locked: false, severity: "critical" },
  { event_type: "sync", channel_id: 1, enabled: true, locked: true, severity: "warning" },
  { event_type: "sync", channel_id: 2, enabled: false, locked: false, severity: "warning" },
  { event_type: "sync", channel_id: 3, enabled: true, locked: false, severity: "warning" },
];

function stubApi(smtpAvailable = true) {
  server.use(
    http.get(apiUrl("/notifications/unread"), () => HttpResponse.json({ count: 0 })),
    http.get(apiUrl("/admin/smtp"), () => HttpResponse.json(smtp(smtpAvailable))),
    http.get(apiUrl("/admin/notification-channels"), () => HttpResponse.json({ items: CHANNELS })),
    http.get(apiUrl("/admin/notification-routes"), () => HttpResponse.json({ items: ROUTES })),
    http.get(apiUrl("/notifications"), () =>
      HttpResponse.json({
        items: [
          {
            id: 10,
            event: "sync.completed",
            event_type: "sync",
            severity: "info",
            title: "Confluence sync finished",
            body: null,
            source: null,
            source_ref: null,
            dedup_count: 1,
            created_at: "2026-07-05T09:00:00Z",
            last_seen_at: null,
            read_at: null,
          },
        ],
        total: 1,
        page: 1,
        per_page: 5,
      }),
    ),
  );
}

describe("NotificationsPage", () => {
  it("renders channels, the matrix with locked pills and the recent feed", async () => {
    stubApi();
    renderAs("owner", <NotificationsPage />);

    expect(await screen.findAllByText("Ops")).not.toHaveLength(0);
    expect(screen.getByText("••••hook")).toBeInTheDocument();
    // two locked cells (in_app × security/sync) render as pills with a lock
    const locked = await screen.findAllByTitle(/Always on/);
    expect(locked).toHaveLength(2);
    // the tail previews the freshest notifications with a door to the full feed
    expect(await screen.findByText("Confluence sync finished")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Show all/ })).toHaveAttribute(
      "href",
      "/admin/notifications/inbox",
    );
    // Personal categories are managed per person — never a matrix row.
    expect(screen.queryByRole("cell", { name: "Agents" })).not.toBeInTheDocument();
    expect(screen.queryByRole("cell", { name: "Account" })).not.toBeInTheDocument();
  });

  it("marks a recent notification read in place", async () => {
    stubApi();
    let read: number | null = null;
    server.use(
      http.post(apiUrl("/notifications/10/read"), () => {
        read = 10;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    renderAs("owner", <NotificationsPage />);

    await screen.findByText("Confluence sync finished");
    await user.click(screen.getByRole("button", { name: "Mark as read" }));
    await waitFor(() => {
      expect(read).toBe(10);
    });
  });

  it("toggles a free matrix cell", async () => {
    stubApi();
    let patched: unknown = null;
    server.use(
      http.patch(apiUrl("/admin/notification-routes"), async ({ request }) => {
        patched = await request.json();
        return HttpResponse.json({ items: ROUTES });
      }),
    );
    const user = userEvent.setup();
    renderAs("owner", <NotificationsPage />);

    // sync × email is Off — clicking turns it on
    const pills = await screen.findAllByRole("button", { name: "Off" });
    await user.click(pills[0]);
    await waitFor(() => {
      expect(patched).toEqual({
        items: [{ event_type: "sync", channel_id: 2, enabled: true }],
      });
    });
  });

  it("is read-only for the Admin role: no webhook actions, disabled switches", async () => {
    stubApi();
    renderAs("admin", <NotificationsPage />);

    expect(await screen.findAllByText("Ops")).not.toHaveLength(0);
    expect(screen.queryByRole("button", { name: "Add webhook" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove" })).not.toBeInTheDocument();
  });

  it("carries the severity chip of each event category in the matrix", async () => {
    stubApi();
    renderAs("owner", <NotificationsPage />);

    // security → critical, sync → warning (from the routes' severity field).
    expect(await screen.findByText("Critical")).toBeInTheDocument();
    expect(screen.getByText("Warning")).toBeInTheDocument();
  });

  it("warns on the Email channel when SMTP is not configured", async () => {
    stubApi(false);
    renderAs("owner", <NotificationsPage />);

    const link = await screen.findByRole("link", { name: /SMTP not configured/ });
    expect(link).toHaveAttribute("href", "/admin/platform#smtp");
  });

  it("marks the Email channel configured when SMTP is available", async () => {
    stubApi(true);
    renderAs("owner", <NotificationsPage />);

    expect(await screen.findByText("SMTP configured")).toBeInTheDocument();
  });

  it("edits a webhook: the form prefills and PATCH carries the changed name", async () => {
    stubApi();
    let patched: unknown = null;
    server.use(
      http.patch(apiUrl("/admin/notification-channels/3"), async ({ request }) => {
        patched = await request.json();
        return HttpResponse.json({ ...CHANNELS[2], name: "Ops renamed" });
      }),
    );
    const user = userEvent.setup();
    renderAs("owner", <NotificationsPage />);

    await screen.findAllByText("Ops");
    await user.click(screen.getByRole("button", { name: "Actions" }));
    await user.click(await screen.findByRole("menuitem", { name: "Edit" }));

    expect(await screen.findByText("Edit webhook")).toBeInTheDocument();
    const nameField = screen.getByLabelText("Name");
    expect(nameField).toHaveValue("Ops");

    await user.clear(nameField);
    await user.type(nameField, "Ops renamed");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(patched).toEqual({ name: "Ops renamed" });
    });
  });

  it("tests a webhook: a good response reports the channel works", async () => {
    stubApi();
    let tested = false;
    server.use(
      http.post(apiUrl("/admin/notification-channels/3/test"), () => {
        tested = true;
        return HttpResponse.json({ ok: true, error: null });
      }),
    );
    const user = userEvent.setup();
    renderAs("owner", <NotificationsPage />);

    await screen.findAllByText("Ops");
    await user.click(screen.getByRole("button", { name: "Actions" }));
    await user.click(await screen.findByRole("menuitem", { name: "Test" }));

    await waitFor(() => {
      expect(tested).toBe(true);
    });
    expect(vi.mocked(toast.success)).toHaveBeenCalledWith(
      expect.stringContaining("the channel works"),
    );
  });

  it("humanizes a webhook-test failure token in the toast", async () => {
    // blocked_host is the SSRF guard's word — the owner must not see a raw token.
    stubApi();
    server.use(
      http.post(apiUrl("/admin/notification-channels/3/test"), () =>
        HttpResponse.json({ ok: false, error: "blocked_host" }),
      ),
    );
    const user = userEvent.setup();
    renderAs("owner", <NotificationsPage />);

    await screen.findAllByText("Ops");
    await user.click(screen.getByRole("button", { name: "Actions" }));
    await user.click(await screen.findByRole("menuitem", { name: "Test" }));

    await waitFor(() => {
      expect(vi.mocked(toast.error)).toHaveBeenCalledWith(
        expect.stringContaining("private or internal address"),
      );
    });
  });

  it("deletes a webhook only behind the confirm", async () => {
    stubApi();
    let deleted = false;
    server.use(
      http.delete(apiUrl("/admin/notification-channels/3"), () => {
        deleted = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    renderAs("owner", <NotificationsPage />);

    await screen.findAllByText("Ops");
    await user.click(screen.getByRole("button", { name: "Actions" }));
    await user.click(await screen.findByRole("menuitem", { name: "Remove" }));

    expect(await screen.findByText("Delete the channel?")).toBeInTheDocument();
    expect(deleted).toBe(false);

    await user.click(screen.getAllByRole("button", { name: "Remove" }).at(-1) as HTMLElement);
    await waitFor(() => {
      expect(deleted).toBe(true);
    });
  });
});
